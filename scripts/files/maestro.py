"""Maestro (the user's own install of a MIDI synth host) as a MIDI out, through its API library by ctypes, like
OmniMIDI (KDMAPI). Never shipped: the library Maestro installs is loaded (Windows: System32\\maestro.dll, a link to
its program folder; Linux libmaestro.so, Mac /usr/local/lib/libmaestro.dylib: untested).

Every message is stamped with the time it should sound (Maestro's clock), so notes keep their exact spacing: sent
without a stamp, its own device quantizes them to its sound blocks (user 2026-11-01: "quantized, rough"). The player
sends LEAD seconds early (MaestroOut.lead, like Kestrel's), so the play line and the sound stay together. Its sound
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
ERR_DISABLED = -3

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


class MaestroError(Exception):
    def __init__(self, disabled, why):
        super().__init__(why)
        self.disabled = disabled  # (the user turned the API / Spiderweb's profile off in Maestro)


class MaestroOut:
    """One open Maestro engine. Messages carry their port in the top byte (as Kestrel's: channel group)."""

    lead = LEAD

    def __init__(self):
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
        self._sync()

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
        """perf_counter time -> Maestro's clock."""
        return int(self.m0 + (when - self.p0) * self.tps)

    def _now_stamp(self):
        """A message to sound now: never before what was sent ahead (a note-off at Stop after its note-on)."""
        return max(self.stamp(time.perf_counter()), self.last)

    def send(self, msg):
        """Sound now (listening, Stop's note-offs)."""
        self.rt.send_short(self.h, (msg >> 24) & 15, msg & 0xFFFFFF, self._now_stamp())

    def send_at(self, msg, when, length=None):
        """Sound at perf_counter time when (sent up to lead early)."""
        at = self.stamp(when)
        if at > self.last:
            self.last = at
        self.rt.send_short(self.h, (msg >> 24) & 15, msg & 0xFFFFFF, at)

    def send_raw(self, port, ptr, size, when=None):
        """size bytes of MIDI messages (running status allowed) at ptr, onto port, at when (None = now)."""
        if when is None:
            at = self._now_stamp()
        else:
            at = self.stamp(when)
            if at > self.last:
                self.last = at
        self.rt.send_long(self.h, port & 15, ptr, size, at)

    def flush(self):
        pass

    def cancel(self):
        pass  # (what was sent ahead is at most lead: it plays, Stop's note-offs come after it)

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
            # (what's queued is dropped by close: a moment for the last note-offs to be played first)
            wait = (self.last - self.rt.now()) / self.tps
            if 0 < wait < 1:
                time.sleep(wait + 0.02)
            self.rt.notes_off(self.h)
            self.rt.close(self.h)
            self.h = _H()
