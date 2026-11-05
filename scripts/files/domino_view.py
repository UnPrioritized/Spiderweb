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
import struct
import hashlib
import os
import re
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
    path_at: int = 0    # S + this -> an object whose + 4 -> the open song's file path (ANSI text, NUL-ended)
    doc_path_at: int = 0  # C + this -> the song's own file path (ANSI; a .dms; for a .mid only "name.dms")
    data_at: int = 0    # builds found by name (files/domino_builds.py): Domino's global data, where the slots are
    data_size: int = 0  # looked for in the running Domino (slots 0 until then)
    ptr: int = 4        # pointer size: 4 (32-bit Domino) or 8 (64-bit: also loaded somewhere else each start)
    file_base: int = 0  # 64-bit: the load address the vtables / data_at are counted from (moved by the real one)
    n_tick: int = 4     # a note + this: tick (u32); selected byte; key byte; velocity byte; gate (u32)
    n_sel: int = 8
    n_key: int = 0xC
    n_vel: int = 0xD
    n_gate: int = 0x10
    undo_n: int = 0x10  # the undo list + this: how many steps it holds
    path_obj: int = 4   # [S + path_at] + this -> the path text
    rows_at: int = 0    # P + this: rows it can scroll + (+ 4) rows on screen = how many keys (128 / 256: the
                        # 256 keys mod); 0 = 128


# sha256 of Domino.exe -> Build, checked by hand. Other Domino versions: found by name (files/domino_builds.py,
# measured 1.41b - 1.44 32-bit); what doesn't add up there = not known, never guessed.
BUILDS = {
    "ba4d007be174626387a59dd0f127d5fd63d0c67cab2219b71aa087ba6725db9d": Build(
        "Domino 1.43 (with dominohook)", p_slot=0x5B49C8, p_vtable=0x55F490, top_row=0xB8, row_px=0x1CC,
        beat_px=0x1D0, h_slot=0x5B4C8C, h_vtable=0x567F4C, left=0x234,
        c_slot=0x5B49F4, c_vtable=0x55D60C, cursor=0x190, song=0x184, ppq_at=0xC,
        tracks_at=0x14, track_now=0x24, track_ch=0x8, events_at=0x18, note_vt=0x55AB28, undo_at=0x188, path_at=0xA8,
        doc_path_at=0x18C),
}
# (shown when a Domino can't be read)
KNOWN_VERSIONS = "Domino 1.41b, 1.42, 1.43, 1.44, and the 64-bit 1.45 test builds (the 256 keys mod too)"
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
    keys: int = 128    # key rows (256 in the 256 keys mod: row 0 = key 255)

    def x_of(self, beat):
        return self.grid[0] + (beat - self.left_beat) * self.beat_px

    def beat_of(self, x):
        return self.left_beat + (x - self.grid[0]) / self.beat_px

    def y_of(self, key):
        """Screen y of the top of a key's row."""
        return self.grid[1] + (self.keys - 1 - key - self.top_row) * self.row_px

    def key_of(self, y):
        """The key whose row holds screen y (outside the keys when y is off them)."""
        return self.keys - 1 - self.top_row - int((y - self.grid[1]) // self.row_px)


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
    """The Build for a Domino.exe, or None when it's a build we can't read. A build found by name has its slots
    0 until with_slots finds them in a running Domino."""
    key = _key(path)
    if key is None:
        return None
    if key not in _known:
        b = BUILDS.get(fingerprint(path))
        if b is None:
            from files import domino_builds
            b = domino_builds.from_file(path, Build)
        _known[key] = b
    return _known[key]


def _key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return os.path.normcase(path), st.st_size, st.st_mtime_ns


_slot_tries = {}  # exe key -> time of the last failed look (looked again after SLOT_AGAIN_S)
SLOT_AGAIN_S = 2.0
# A Domino found by name whose numbers don't add up (hunt 2026-11-06): after ODD_AFTER failed looks WITH ITS PIANO
# ROLL SHOWN (still starting doesn't count) it counts as a version not known: refused with the "isn't known" message,
# never a forever "Can't read". The user may still say "Try anyway" (user 2026-11-06: their choice, warned in red):
# then the spots found are used without the checks, for this run of that Domino only.
ODD_AFTER = 3
_odd = {}        # exe key (+ load address) -> (failed looks, the build with its spots found or None)
_forced = set()  # exe keys (+ load address) the user said "Try anyway" for (until Spiderweb closes)


def with_slots(d):
    """A Domino whose build was found by name: its slots looked for in the running Domino (a 32-bit Domino: once
    per program file, it never moves; a 64-bit one: once per load address). Not found yet (still starting, no
    piano roll) = looked again later; till then reading answers "not readable"."""
    import time
    b = d.build
    if b is None or b.p_slot or not b.data_at:
        return d
    key = _key(d.path)
    ph = d.handle()
    if key is None or not ph:
        return d
    if b.ptr == 8:  # (64-bit: everything moved by where Windows loaded it this time)
        base = _module_base(ph)
        if not base:  # (e.g. Spiderweb run by a 32-bit Python: a 64-bit Domino can't be read from there)
            return _failed(d, key, None) if time.monotonic() - _slot_tries.get(key, -1e9) >= SLOT_AGAIN_S else \
                _mark(d, key)
        key += (base,)
        if key in _known:
            d.build = _known[key]
            return d
        b = moved(b, base - b.file_base)
    d.key = key
    got = _odd.get(key, (0, None))[1]
    if key in _forced and got is not None:
        d.build, d.forced = got, True
        return d
    if time.monotonic() - _slot_tries.get(key, -1e9) < SLOT_AGAIN_S:
        return _mark(d, key)
    from files import domino_builds
    read = lambda a, n: _read(ph, a, n)  # noqa: E731
    got = domino_builds.find_slots(b, read, check=False)
    if got is not None and domino_builds.adds_up(got, read):
        _known[key] = d.build = got
        _odd.pop(key, None)
        d.odd = False
        return d
    return _failed(d, key, got)


def _failed(d, key, got):
    """A look that found nothing readable: counted only while Domino shows its piano roll."""
    import time
    _slot_tries[key] = time.monotonic()
    d.key = key
    n, old = _odd.get(key, (0, None))
    if system.WINDOWS and grid_window(d.top) is not None:
        _odd[key] = (n + 1, got or old)
    return _mark(d, key)


def _mark(d, key):
    d.odd = _odd.get(key, (0, None))[0] >= ODD_AFTER
    return d


def can_force(d):
    """Can "Try anyway" read anything in this Domino (its spots were found, only the checks failed)?"""
    return bool(d.odd and d.key is not None and _odd.get(d.key, (0, None))[1] is not None)


def force(d):
    """The user's "Try anyway" (warned first): the spots found are used without the checks, for this run."""
    if can_force(d):
        _forced.add(d.key)
        d.build, d.forced, d.odd = _odd[d.key][1], True, False
    return d


def settle(d):
    """At the button: a Domino whose spots aren't found yet is looked at again at once, so a version that doesn't
    add up is told now (still starting = no answer yet: the overlay waits as before)."""
    import time
    for _ in range(ODD_AFTER):
        if d.build is None or d.build.p_slot or d.odd:
            break
        for k in (d.key, _key(d.path)):
            _slot_tries.pop(k, None)
        with_slots(d)
        if not d.odd and not d.build.p_slot:
            time.sleep(0.15)
    return d


def moved(b, delta):
    """A 64-bit build's addresses moved to where this Domino was loaded."""
    from dataclasses import replace
    return replace(b, p_vtable=b.p_vtable + delta, h_vtable=b.h_vtable + delta, c_vtable=b.c_vtable + delta,
                   note_vt=b.note_vt + delta, data_at=b.data_at + delta, file_base=b.file_base + delta)


def _module_base(ph):
    """Where Windows loaded the program of a process (its first module), or 0."""
    try:
        psapi = ctypes.windll.psapi
        mods = (ctypes.c_void_p * 4)()
        need = W.DWORD()
        psapi.EnumProcessModulesEx.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), W.DWORD]
        if psapi.EnumProcessModulesEx(ph, mods, ctypes.sizeof(mods), ctypes.byref(need), 3):  # LIST_MODULES_ALL
            return mods[0] or 0
    except (OSError, AttributeError):
        pass
    return 0


def view_from(numbers, grid_win, scale, keys=128):
    """ViewState from the raw numbers (top_row, row_px, beat_px, left units), the grid window's screen rect
    (left, top, right, bottom; its right edge = the scroll bar's left) and the scale, + the build's edges."""
    top_row, row_px, beat_px, left, keys_w, ruler_h = numbers
    x0 = grid_win[0] + keys_w * scale
    y0 = grid_win[1] + ruler_h * scale
    bottom = min(grid_win[3], y0 + (keys - top_row) * row_px * scale)
    return ViewState(left / UNITS_PER_BEAT, beat_px * scale, top_row, row_px * scale,
                     (round(x0), round(y0), grid_win[2], round(bottom)), scale, keys)


ROWS_OFF_MOST = 40  # (reads: about 0.6 s of ticks)


def rows_fit(nums, grid_h, scale, ruler_h):
    """Do the rows Domino says are on screen fill its grid (within 2 rows)? 1.45: 57 rows x 12 px in 686 px."""
    row_px = nums[1]
    return abs(nums[5] - (grid_h / (scale or 1) - ruler_h) / row_px) <= 2


def sane(numbers, keys=128):
    top_row, row_px, beat_px, left = numbers[:4]
    return 0 <= top_row < keys and 1 <= row_px <= 200 and 1 <= beat_px <= 10000 and 0 <= left < 2 ** 31


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
    key: object = None   # (its exe key, + load address when 64-bit: with_slots)
    odd: bool = False    # found by name, but its numbers didn't add up (a version not known: refused)
    forced: bool = False  # read anyway, the user's "Try anyway" (no checks)
    rows_off: int = 0    # (reads in a row whose key rows didn't match the grid's height)

    def handle(self):
        if not self._handle:
            _, k = _api()
            self._handle = k.OpenProcess(0x0410, False, self.pid) or 0  # VM_READ | QUERY_INFORMATION
        return self._handle

    def close(self):
        if self._handle:
            _api()[1].CloseHandle(self._handle)
            self._handle = 0


_alive = {}  # pid -> Domino: kept between looks, so each running Domino has ONE process handle (hunt 2026-11-06:
# a new one every half second was never closed); closed when that Domino is gone


def is_domino(path, saved=""):
    """A program Spiderweb follows: named Domino*.exe (renamed copies like "Domino (2).exe", "Domino145.exe"), or
    the Domino.exe picked in Spiderweb (settings.ini [domino] exe) whatever its name (hunt 2026-11-06). Its version
    check still decides whether it can be read."""
    name = os.path.basename(path).lower()
    if name.startswith("domino") and name.endswith(".exe"):
        return True
    return bool(saved and path) and os.path.normcase(saved) == os.path.normcase(path)


def find_all():
    """Every running Domino with a visible main window, front-most first."""
    if not system.WINDOWS:
        return []
    from files import settings
    saved = settings.get("domino", "exe") or ""
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
        if any(d.pid == pid for d in found):
            continue
        path = exe_path(pid)
        if not is_domino(path, saved):
            continue
        d = _alive.get(pid)
        if d is None or os.path.normcase(d.path) != os.path.normcase(path):
            if d is not None:
                d.close()
            d = _alive[pid] = Domino(h, pid, path, build_of(path))
        d.top = h
        found.append(with_slots(d))
    for pid in [p for p in _alive if not any(d.pid == p for d in found)]:
        _alive.pop(pid).close()
    return found


_GRID_CLASS = re.compile(r"Afx:[0-9A-Fa-f]+:8:")  # (32-bit "Afx:400000:8:", 64-bit the address in 16 digits)


def grid_window(top):
    """Domino's note grid window: the biggest visible 'Afx:<load address>:8:' window with a narrow child (its own
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
        if not _GRID_CLASS.match(_class(h)):
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


def _p(ph, addr, b):
    """A pointer (the build's size) read from Domino's memory, or 0."""
    raw = _read(ph, addr, b.ptr) if addr else b""
    return int.from_bytes(raw, "little") if len(raw) == b.ptr else 0


def _obj(ph, slot, vtable, b):
    """The object a slot points at, when its vtable is the expected one, else 0."""
    o = _p(ph, slot, b)
    return o if o and _p(ph, o, b) == vtable else 0


def read_numbers(d):
    """(top_row, row_px, beat_px, left units, keys, rows on screen or 0) read from Domino's memory, or None."""
    b, ph = d.build, d.handle()
    if b is None or not ph:
        return None
    p, h = _obj(ph, b.p_slot, b.p_vtable, b), _obj(ph, b.h_slot, b.h_vtable, b)
    if not p or not h:
        return None
    keys, shown = 128, 0
    if b.rows_at:  # (rows it can scroll + rows on screen: exactly 128 or 256, anything else = not this place)
        a, c = _i32(ph, p + b.rows_at), _i32(ph, p + b.rows_at + 4)
        if a is None or c is None or a + c not in (128, 256) or c < 1:
            return None
        keys, shown = a + c, c
    out = (_i32(ph, p + b.top_row), _i32(ph, p + b.row_px), _i32(ph, p + b.beat_px), _i32(ph, h + b.left))
    return out + (keys, shown) if None not in out and sane(out, keys) else None


def song_place(d):
    """(cursor tick, PPQ) read from Domino's memory, or None (unknown build / not readable). The cursor is where
    Domino pastes; after a copy it is the copied stretch's start."""
    got = _song_obj(d)
    if got is None:
        return None
    ph, c, s = got
    b = d.build
    tick = _i32(ph, c + b.cursor)
    ppq = _i32(ph, s + b.ppq_at)
    if tick is None or ppq is None or not 0 <= tick < 2 ** 31 or not 1 <= ppq <= 65535:
        return None
    return tick, ppq


def _song_obj(d):
    """(process handle, C, S) or None: the song position object and the song, checked."""
    b = getattr(d, "build", None)
    if b is None or not b.c_slot:
        return None
    ph = d.handle()
    if not ph:
        return None
    c = _obj(ph, b.c_slot, b.c_vtable, b)
    s = _p(ph, c + b.song, b) if c else 0
    return (ph, c, s) if s else None


def undo_depth(d):
    """How many steps Domino's undo list holds (changes with every edit, undo, redo), or None. Found 2026-11-06
    (dev/scratch/dk_undo.py, dev/data/domino-notes-memory.md)."""
    got = _song_obj(d)
    if got is None or not d.build.undo_at:
        return None
    ph, c, _ = got
    u = _p(ph, c + d.build.undo_at, d.build)
    return _i32(ph, u + d.build.undo_n) if u else None


def _track_list(d):
    """(process handle, [each track's object]) in track-list order, or None."""
    got = _song_obj(d)
    b = d.build
    if got is None or not b.tracks_at:
        return None
    ph, _, s = got
    arr, n = _p(ph, s + b.tracks_at, b), _i32(ph, s + b.tracks_at + b.ptr)
    if not arr or n is None or not 1 <= n <= 65535:
        return None
    raw = _read(ph, arr, b.ptr * n)
    if len(raw) != b.ptr * n:
        return None
    return ph, list(struct.unpack(f"<{n}{'Q' if b.ptr == 8 else 'I'}", raw))


def track_ids(d):
    """Each track's object in Domino's memory, in track-list order (stays the same while the track exists and the
    same Domino runs: tracks added / removed / moved above are found again by it), or None."""
    got = _track_list(d)
    return got[1] if got else None


def song_path(d):
    """The file Domino has open (full path), "" for a song never saved, or None (unknown / unreadable). Found
    2026-11-06 (dev/scratch/dk_path.py: [[S + 0xA8] + 4] = the path text, two files checked)."""
    got = _song_obj(d)
    b = d.build if got else None
    if got is None or not (b.path_at or b.doc_path_at):
        return None
    ph, c, s = got
    if b.doc_path_at:  # (2026-11-06: a .dms song's path is only here; a .mid's holds just "name.dms")
        own = _text_at(ph, _p(ph, c + b.doc_path_at, b))
        if own:
            return own
    if not b.path_at:
        return ""
    q = _p(ph, s + b.path_at, b)
    p = _p(ph, q + b.path_obj, b) if q else 0
    if not p:
        return ""
    return _text_at(ph, p) if _read(ph, p, 1)[:1] not in (b"", b"\0") else ""


def _text_at(ph, p):
    """A full file path (ANSI text) at p, or "" (none there / not a full path)."""
    if not p:
        return ""
    raw = _read(ph, p, 1040).split(b"\0", 1)[0]
    try:
        text = raw.decode("mbcs")
    except (UnicodeDecodeError, LookupError):
        return ""
    return text if len(text) > 3 and (text[1:3] == ":\\" or text.startswith("\\\\")) else ""


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
    out = _track_events(d, track)
    return None if out is None else out[out[:, 0] == d.build.note_vt, 1:]


def track_others(d, track):
    """The ticks of the track's events that aren't notes (control changes, track name, ...), or None. Their tick
    sits where a note's does (measured 2026-11-06, dev/scratch/rm_others.py)."""
    out = _track_events(d, track)
    return None if out is None else out[out[:, 0] != d.build.note_vt, 1]


def _track_events(d, track):
    """Every event of a track: (vtable, tick, gate, key, velocity, selected) rows (only notes' last four mean
    anything), or None."""
    import numpy as np
    b = d.build
    got = _track_list(d) if b is not None and b.events_at else None
    if got is None or not 0 <= track < len(got[1]):
        return None
    ph, tracks = got
    t = tracks[track]
    if not t:
        return None
    ev, m = _p(ph, t + b.events_at, b), _i32(ph, t + b.events_at + b.ptr)
    if m is None or not 0 <= m <= 50_000_000:
        return None
    if m == 0:
        return np.zeros((0, 6), np.int64)
    raw = _read(ph, ev, b.ptr * m)
    if len(raw) != b.ptr * m:
        return None
    ptrs = np.frombuffer(raw, np.uint64 if b.ptr == 8 else np.uint32).astype(np.int64)
    span = max(b.n_gate, b.n_tick) + 4  # (a note's bytes read)
    pages = np.unique(np.concatenate([ptrs >> 12, (ptrs + span - 1) >> 12]))
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
        off = off[off + span <= len(data)]
        sel = sel[:len(off)]
        word = lambda o: data[(off + o)[:, None] + np.arange(4)].copy().view(np.uint32).ravel()  # noqa: E731
        if b.ptr == 8:  # (the vtable: 8 bytes)
            out[sel, 0] = data[off[:, None] + np.arange(8)].copy().view(np.uint64).ravel().astype(np.int64)
        else:
            out[sel, 0] = word(0)
        out[sel, 1] = word(b.n_tick).view(np.int32)
        out[sel, 2] = word(b.n_gate).view(np.int32)
        out[sel, 3] = data[off + b.n_key]
        out[sel, 4] = data[off + b.n_vel]
        out[sel, 5] = data[off + b.n_sel] != 0
    return out


def song_tracks(d):
    """(current track, [each track's channel, 0-based]) read from Domino's memory, or None. Track 0 = Conductor.
    Found 2026-11-06 on 1.43 (dev/scratch/di_probe.py: a MIDI with known channels, Domino's track commands)."""
    b = getattr(d, "build", None)
    got = _song_obj(d) if b is not None and b.tracks_at else None
    tl = _track_list(d) if got else None
    if tl is None:
        return None
    ph, _, s = got
    now = _i32(ph, s + b.track_now)
    ptrs = tl[1]
    if now is None or not 0 <= now < len(ptrs):
        return None
    chans = []
    for p in ptrs:
        one = _read(ph, p + b.track_ch, 1) if p else b""
        if not one:
            return None
        chans.append(one[0] & 15)
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
        if nums[5] and not rows_fit(nums, win[3] - win[1], now[2], d.build.ruler_h):
            # (the rows on screen say how tall the grid is: a key count read from the wrong place shows here,
            # measured 2026-11-06; a resize may be half done: wrong for a while = not readable)
            d.rows_off += 1
            if d.rows_off > ROWS_OFF_MOST:
                return None, "not readable"
            last = None
            continue
        d.rows_off = 0
        if now == last:
            n, rect, scale = now
            return view_from(n[:4] + (d.build.keys_w, d.build.ruler_h), rect, scale, n[4]), None
        last = now
    return None, "busy"
