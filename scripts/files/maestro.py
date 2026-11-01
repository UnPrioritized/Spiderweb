"""Maestro (the user's own install of a MIDI synth host) as a MIDI out, through its API library by ctypes, like
OmniMIDI (KDMAPI). Never shipped: the library Maestro installs is loaded (Windows: System32\\maestro.dll, a link to
its program folder; Linux libmaestro.so, Mac /usr/local/lib/libmaestro.dylib: untested).

Every message is stamped with the time it should sound (Maestro's clock), so notes keep their exact spacing: sent
without a stamp, its own device quantizes them to its sound blocks (user 2026-11-01: "quantized, rough"). Maestro
plays a stamp `delay` later (its buffers, ~0.1 s; read at each Play): stamps are that much earlier and the player
sends LEAD + delay ahead (MaestroOut.lead, like Kestrel's), so the sound lands on the play line. Its sound
settings (soundfonts, synth, limiter) live in Maestro's own window: Spiderweb gets its own profile there
(show_settings). Up to 16 ports: a loaded MIDI's tracks keep their own (user 2026-11-01)."""

import ctypes
import os
import sys
import threading
import time

from files.system import WINDOWS

APP_ID = b"io.github.unprioritized.spiderweb"
APP_NAME = b"Spiderweb"
PORTS = 16
LEAD = 0.05  # seconds early the player sends (Python may wake up this late and every note still keeps its time)
OK = 0
ERR_INVALID_ARG = -1
ERR_DISABLED = -3
NOTIFY_STOPPED = 1  # (its engine failed to restart)
NOTIFY_DISABLED = 3  # (the user turned this program's profile off; 0.2.2 and older: or the whole API)
DELAY_SETTLE_S = 1.2  # (just opened: its delay waited for at most this long after opening...)
DELAY_STILL_S = 0.25  # (... until it held still this long)
DELAY_SOON_S = 0.5  # (and never trusted sooner than this after opening)
DELAY_GUESS_S = 0.1  # (never told: about what it was here, 2026-11-01: 112 ms)
DELAY_MOST_S = 1.0
DRAIN_MOST_S = 1.5  # (longer = something's wrong with the clocks: never waited for)
DRAIN_EXTRA_S = 0.005
NOT_RUNNING_S = 3.0  # (sends refused this long = its engine is down, not restarting for a settings change)

_CALL = ctypes.CFUNCTYPE  # (MAESTRO_CALL = __cdecl on Windows too)
_LogFn = _CALL(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p)
_NotifyFn = _CALL(None, ctypes.c_void_p, ctypes.c_uint32)
_H, _R, _T = ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint64


class _App(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("reserved", ctypes.c_uint32), ("app_id", ctypes.c_char_p),
                ("app_name", ctypes.c_char_p), ("log", _LogFn), ("user", ctypes.c_void_p)]


class _Params(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("num_ports", ctypes.c_uint32), ("midi2", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32), ("notify", _NotifyFn)]


class _Info(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("sample_rate", ctypes.c_uint32), ("num_ports", ctypes.c_uint32),
                ("midi2", ctypes.c_uint32), ("render_block_us", ctypes.c_uint32),
                ("max_schedule_us", ctypes.c_uint32)]


class _Stats(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("render_time", ctypes.c_float), ("latency_us", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32), ("voice_count", ctypes.c_uint64), ("nps", ctypes.c_uint64),
                ("eps", ctypes.c_uint64), ("nps_limit", ctypes.c_uint64)]


class _Core(ctypes.Structure):
    _fields_ = [("get_version", _CALL(ctypes.c_uint32)), ("last_error", _CALL(ctypes.c_char_p))]


class _Realtime(ctypes.Structure):  # (maestro.realtime/1, the header's order)
    _fields_ = [("open", _CALL(_R, ctypes.POINTER(_App), ctypes.POINTER(_Params), ctypes.POINTER(_H))),
                ("close", _CALL(None, _H)),
                ("now", _CALL(_T)),
                ("ns_to_ticks", _CALL(_T, ctypes.c_uint64)),
                ("send_short", _CALL(_R, _H, ctypes.c_uint8, ctypes.c_uint32, _T)),
                ("send_long", _CALL(_R, _H, ctypes.c_uint8, ctypes.c_void_p, ctypes.c_size_t, _T)),
                ("send_ump", _CALL(_R, _H, ctypes.c_void_p, ctypes.c_size_t, _T)),
                ("get_info", _CALL(_R, _H, ctypes.POINTER(_Info))),
                ("get_stats", _CALL(_R, _H, ctypes.POINTER(_Stats))),
                ("reset", _CALL(_R, _H)),
                ("notes_off", _CALL(_R, _H)),
                ("show_settings", _CALL(_R, _H))]


def lib_path():
    """Where Maestro's API library is, or None (Maestro not installed)."""
    if WINDOWS:
        path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "maestro.dll")
        if not os.path.isfile(path):
            return None
        # (the link's target: loaded from Maestro's own folder, its other libraries beside it)
        try:
            return os.path.realpath(path)
        except OSError:
            return path
    if sys.platform == "darwin":
        path = "/usr/local/lib/libmaestro.dylib"
        return path if os.path.isfile(path) else None
    if not _found:  # (the search starts other programs: done once)
        import ctypes.util
        _found.append(ctypes.util.find_library("maestro"))
    return _found[0]


_found = []
_lib = None  # (lib, core, realtime) once loaded


def load():
    """(lib, core, realtime tables), or raises OSError (not found / too old)."""
    global _lib
    if _lib is None:
        path = lib_path()
        if not path:
            raise OSError("not found")
        lib = ctypes.CDLL(path)
        lib.maestro_get_interface.restype = ctypes.c_void_p
        lib.maestro_get_interface.argtypes = [ctypes.c_char_p]
        core = lib.maestro_get_interface(b"maestro.core/1")
        rt = lib.maestro_get_interface(b"maestro.realtime/1")
        if not core or not rt:
            raise OSError("too old")
        _lib = (lib, ctypes.cast(core, ctypes.POINTER(_Core)).contents,
                ctypes.cast(rt, ctypes.POINTER(_Realtime)).contents)
    return _lib


def version():
    """Maestro's version as "0.2", or None."""
    try:
        v = load()[1].get_version()
    except OSError:
        return None
    return f"{v >> 16}.{v & 0xFFFF}"


def gs_drum_part(ch, drums):
    """GS "use for rhythm part": channel ch (0-15) plays drums (1) or an ordinary instrument (0)."""
    part = 0x10 | (0 if ch == 9 else ch + 1 if ch < 9 else ch)  # (GS parts: 10 first, then 1-9, 11-16)
    body = [0x40, part, 0x15, drums]
    return bytes([0xF0, 0x41, 0x10, 0x42, 0x12] + body + [(128 - sum(body) % 128) % 128, 0xF7])


class MaestroError(Exception):
    def __init__(self, disabled, why):
        super().__init__(why)
        self.disabled = disabled  # (the user turned Spiderweb's profile off in Maestro)


class MaestroOut:
    """One open Maestro engine. Messages carry their port in the top byte (as Kestrel's: channel group)."""

    lead = LEAD

    def __init__(self):
        self.delay = 0.0  # (Maestro's own: a stamp sounds this much later; refresh)
        _, self.core, self.rt = load()
        self.log = _LogFn(self._on_log)  # (kept: ctypes frees a callback nobody holds)
        self.notify = _NotifyFn(self._on_notify)
        self.app = _App(struct_size=ctypes.sizeof(_App), app_id=APP_ID, app_name=APP_NAME, log=self.log)
        params = _Params(struct_size=ctypes.sizeof(_Params), num_ports=PORTS, notify=self.notify)
        self.h = _H()
        self.messages = []  # (its log lines, the last few)
        self.event = None  # (its last notify: 1 = engine stopped, 3 = turned off)
        r = self.rt.open(ctypes.byref(self.app), ctypes.byref(params), ctypes.byref(self.h))
        if r != OK:
            raise MaestroError(r == ERR_DISABLED, (self.core.last_error() or b"").decode("utf-8", "replace"))
        self._lock = threading.Lock()
        self.tps = self.rt.ns_to_ticks(1_000_000_000)  # (its clock's ticks a second)
        self.last = 0  # (latest stamp sent: a message sent "now" never comes before one queued earlier)
        self.failed = None  # (a send Maestro refused: turned off / its engine stopped; since failed_at)
        self.failed_at = 0.0
        self.ons = 0  # (note-ons sent: "Overloaded" against its load limiter; a loaded MIDI's reader adds its own)
        self.opened = time.perf_counter()
        self._sync()

    def problem(self):
        """None, or what's wrong: "disabled" (the user turned Spiderweb's profile off in Maestro), "stopped"
        (its engine failed to start, or kept refusing sends for NOT_RUNNING_S: a settings change restarts it)."""
        if self.event == NOTIFY_DISABLED or self.failed == ERR_DISABLED:
            return "disabled"
        if self.event == NOTIFY_STOPPED or (
                self.failed is not None and time.perf_counter() - self.failed_at > NOT_RUNNING_S):
            return "stopped"
        return None

    def refresh(self):
        """At each Play: Maestro's own delay read again (its settings may have changed it) and the clocks lined up.
        Returns problem()."""
        why = self.problem()
        if why:
            return why
        # (0 until its sound starts, then it climbs for a moment: 48 -> 87 -> 112 ms here; just opened = waited until
        # it holds still)
        delay, since, until = 0, None, self.opened + DELAY_SETTLE_S
        while True:
            st = self.stats()
            now = time.perf_counter()
            got = st["latency_us"] if st else 0
            if got != delay:
                delay, since = got, now
            if now > until or (delay and now - since >= DELAY_STILL_S and now - self.opened > DELAY_SOON_S):
                break
            time.sleep(0.02)
        # (stamped this much early, the sound lands on the play line, user 2026-11-01: "noticeably delayed")
        self.delay = min(delay / 1e6, DELAY_MOST_S) if delay else DELAY_GUESS_S
        self.lead = LEAD + self.delay
        self._sync()
        return None

    def _sync(self):
        """Its clock against perf_counter: the closest of a few reads."""
        best = None
        for _ in range(8):
            a = time.perf_counter()
            m = self.rt.now()
            b = time.perf_counter()
            if best is None or b - a < best[0]:
                best = (b - a, (a + b) / 2, m)
        self.p0, self.m0 = best[1], best[2]

    def _on_log(self, user, level, msg):
        self.messages = (self.messages + [(msg or b"").decode("utf-8", "replace")])[-5:]

    def _on_notify(self, user, what):
        self.event = what

    def stamp(self, when):
        """perf_counter time the sound should be heard -> Maestro's clock (its own delay taken off)."""
        return int(self.m0 + (when - self.delay - self.p0) * self.tps)

    def _keep(self, at):
        """at is the latest stamp sent so far? (the player and a loaded MIDI's reader send from two threads)"""
        with self._lock:
            if at > self.last:
                self.last = at

    def _now_stamp(self):
        """A message to sound now: never before what was sent ahead (a note-off at Stop after its note-on)."""
        with self._lock:
            return max(self.rt.now(), self.last)

    def _sent(self, r):
        if r == OK:
            if self.failed is not None:
                self.failed = None
        elif r != ERR_INVALID_ARG and self.failed is None:
            self.failed, self.failed_at = r, time.perf_counter()

    def send(self, msg):
        """Sound now (listening, Stop's note-offs)."""
        self._sent(self.rt.send_short(self.h, (msg >> 24) & 15, msg & 0xFFFFFF, self._now_stamp()))

    def send_at(self, msg, when, length=None):
        """Sound at perf_counter time when (sent up to lead early)."""
        at = self.stamp(when)
        self._keep(at)
        if msg & 0xF0 == 0x90 and msg & 0x7F0000:
            self.ons += 1
        self._sent(self.rt.send_short(self.h, (msg >> 24) & 15, msg & 0xFFFFFF, at))

    def send_raw(self, port, ptr, size, when=None):
        """size bytes of MIDI messages (running status allowed) at ptr, onto port, at when (None = now)."""
        if when is None:
            at = self._now_stamp()
        else:
            at = self.stamp(when)
            self._keep(at)
        self._sent(self.rt.send_long(self.h, port & 15, ptr, size, at))

    def drum_parts(self, drums):
        """Channel 10 on each port: drums (ports in `drums`) or an ordinary instrument (GS "use for rhythm part"),
        as Built-in BASSMIDI / Kestrel play Spiderweb's own shapes; a loaded MIDI's ports keep their drums."""
        for port in range(PORTS):
            raw = gs_drum_part(9, 1 if port in drums else 0)
            buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
            self.send_raw(port, ctypes.addressof(buf), len(raw))

    def flush(self):
        pass

    def drain(self):
        """Waits until everything sent ahead has started (at most lead). Maestro 0.2.2 and older played controller
        messages at once (its "coalesce" setting, removed in 0.2.3), so an "all notes off" sent before then came
        before a note sent ahead: that note rang forever (hunt 2026-11-01). Kept for those versions: its API can't
        tell 0.2.2 from 0.2.3 (get_version = major.minor only)."""
        if not self.h:
            return
        wait = (self.last - self.rt.now()) / self.tps
        if 0 < wait < DRAIN_MOST_S:
            time.sleep(wait + DRAIN_EXTRA_S)

    def cancel(self):
        """Stop: what was sent ahead plays first (Maestro can't drop it alone), so Stop's note-offs end it."""
        self.drain()

    def stats(self):
        st = _Stats(struct_size=ctypes.sizeof(_Stats))
        if self.rt.get_stats(self.h, ctypes.byref(st)) != OK:
            return None
        return {"load": st.render_time, "latency_us": st.latency_us, "voices": st.voice_count, "nps": st.nps,
                "nps_limit": st.nps_limit}

    def show_settings(self):
        """Maestro's settings for Spiderweb's profile. True when it opened."""
        return self.rt.show_settings(self.h) == OK

    def close(self):
        if self.h:
            self.drain()  # (what's queued is dropped by close: the last note-offs played first)
            self.rt.notes_off(self.h)
            self.rt.close(self.h)
            self.h = _H()
