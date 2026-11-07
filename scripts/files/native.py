"""Our own compiled loops: a library built from native/loops (Rust), loaded by ctypes like BASS. It replaces the
Numba loops of files/fastloops.py one by one (PLAN.md B): the loops it has are used in their place, every other one
still comes from fastloops. Each gives exactly what the NumPy code (and the Numba loop) gives: dev/tests/native_loops
compares them. No library (not built, another system, a 32-bit Python, SPIDERWEB_NATIVE=off) = fastloops as before.

The library holds every loop four times (x86-64 levels: any, SSE4.2, AVX2, AVX-512) and picks the highest the
processor has (SPIDERWEB_NATIVE_LEVEL=1-4 forces one, for checks). CHEAP CALLS (PLAN.md B:
never slower than Numba, small inputs too): the same file is ALSO loaded as a Python extension module (pyext.rs:
a call ~0.4 us, ctypes' own floor is ~1 us, Numba's ~0.8 us; load_ext; it fails = the ctypes functions, the same
loops). Arrays are handed over as they are (the library reads NumPy's array struct, checked at load by probe()); one it can't read as it is (another type, spread out in memory, read
only) = NEED_COPY, made plain here and handed over again. A loop that fails inside (a bad index: caught there, it
never ends the program; no memory) returns below 0 and that call uses the fastloops loop instead, noted once in the
logs.
  stir_spots, stir_spots_one   recolour.stir_from's middle (stir_spots on half the cores past CORES_LEAST spots)
  winner_pieces                engine.winner_stretches' pieces (Remove overlaps, split around)
  key_order, key_order_cores   np.lexsort by key, then 3 more columns (key_order_cores: the key groups on half
                               the cores past CORES_LEAST rows)"""

import ctypes
import os
import platform
import struct
import sys

import numpy as np

from files.about import HERE
from files.system import MAC, WINDOWS

ABI = 3  # (the library's sw_abi(): another one = not used)
LEVELS = {1: "plain", 2: "SSE4.2", 3: "AVX2", 4: "AVX-512"}  # (sw_level: the builds in use)
CORES_LEAST = 20000  # (rows: fewer = one core, sharing out costs more than it saves; as fastloops.CORES_LEAST)
NEED_COPY = -3

_dll = None
_ext = None  # (the same file as an extension module: the cheap calls; None = ctypes')
_why = None  # (why there's no library: shown in the logs' state line)
_failed = set()  # (loops that failed once: said once in the logs)


def lib_path():
    """Where the library is: scripts/loops/<system>/ (the exe: its own loops/ folder)."""
    base = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "loops")
    if WINDOWS:
        return os.path.join(base, "x64", "spiderweb_loops.dll")
    if MAC:
        return os.path.join(base, "mac", "libspiderweb_loops.dylib")
    return os.path.join(base, "linux-x64", "libspiderweb_loops.so")


def cores():
    """How many threads a shared-out loop uses: half the cores (the synths' rule: all of them halted the PC)."""
    return max(1, (os.cpu_count() or 2) // 2)


def probe(d):
    """Does the library read NumPy's arrays right here (place, length, type, stride, flags)? (Another Python build,
    e.g. free-threaded, has another object head: then the library isn't used.)"""
    out = np.zeros(5, np.int64)
    for a in (np.arange(7, dtype=np.int64), np.linspace(0, 1, 5), np.zeros(0, np.int64), np.zeros(9)[::3]):
        d.sw_probe(a, out.ctypes.data)
        if out.tolist() != [a.ctypes.data, len(a), a.dtype.num, a.strides[0], a.flags.num]:
            return False
    return True


def load():
    """The library, loaded once; None when it can't be (why() says why)."""
    global _dll, _why
    if _dll or _why:
        return _dll
    if os.environ.get("SPIDERWEB_NATIVE", "") == "off":
        _why = "turned off"
        return None
    if struct.calcsize("P") != 8 or platform.machine().lower() not in ("amd64", "x86_64"):
        _why = "not built for this system"
        return None
    path = lib_path()
    if not os.path.isfile(path):
        _why = "not built"
        return None
    try:
        d = ctypes.CDLL(path)
        i64, o = ctypes.c_int64, ctypes.py_object
        for name, args in (("sw_abi", []), ("sw_level", []), ("sw_set_level", [i64]), ("sw_panic_check", []),
                           ("sw_setup", [i64] * 5), ("sw_probe", [o, ctypes.c_void_p]),
                           ("sw_stir_spots", [o] * 6 + [i64]), ("sw_winner_pieces", [o] * 8),
                           ("sw_key_order", [o] * 5 + [i64])):
            f = getattr(d, name)
            f.argtypes, f.restype = args, i64
        if d.sw_abi() != ABI:
            _why = "another version (%d)" % d.sw_abi()
            return None
        ints = [np.dtype(t).num for t in (np.int64, np.longlong, np.int_) if np.dtype(t).itemsize == 8
                and np.dtype(t).kind == "i"]
        d.sw_setup(ints[0], ints[-1], np.dtype(np.float64).num, cores(), CORES_LEAST)
        force = os.environ.get("SPIDERWEB_NATIVE_LEVEL", "")  # (checks: 1-4 = that level, if this processor has it)
        if force.isdigit():
            d.sw_set_level(int(force))
        if not probe(d):
            _why = "can't read this Python's arrays"
            return None
    except (OSError, AttributeError) as e:
        _why = "failed: %s" % e
        return None
    _dll = d
    global _ext
    _ext = load_ext(path)
    return d


def load_ext(path):
    """The library file as a Python extension module (its PyInit, pyext.rs): the same loops as the ctypes functions
    without ctypes' call cost. Loaded from the same file, so it shares sw_setup's numbers (checked by one tiny call).
    None when it can't be (SPIDERWEB_NATIVE_EXT=off too): the ctypes functions are used."""
    if os.environ.get("SPIDERWEB_NATIVE_EXT", "") == "off":
        return None
    try:
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.ExtensionFileLoader("spiderweb_loops", path)
        spec = importlib.util.spec_from_file_location("spiderweb_loops", path, loader=loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        z = np.zeros(1, np.int64)
        if m.key_order(z, z, z, z, np.empty(1, np.int64), 0) != 0:  # (another copy of the file: NEED_COPY)
            return None
        return m
    except Exception:  # noqa: BLE001 (any failure: the ctypes calls)
        return None


def why():
    """Why there's no library ("" when there is one)."""
    return "" if _dll else (_why or "not loaded yet")


def level_name():
    """"AVX-512" / "AVX2" / "SSE4.2" / "plain": the builds the library uses (the logs' state line)."""
    return (LEVELS.get(_dll.sw_level(), "plain") + ("" if _ext else ", ctypes calls")) if _dll else "plain"


def over(base):
    """The loops the program uses: base (the fastloops module) with the library's loops in their place; base as it
    is without the library."""
    d = load()
    return Loops(base, d, _ext) if d else base


def _in(a, dtype):
    """a as the library reads it: 1-D, side by side, of that type (a copy only when it isn't)."""
    return np.ascontiguousarray(a, dtype)


def _failed_once(name, code):
    if name not in _failed:
        _failed.add(name)
        from files import errors
        errors.note("own loops", "%s failed (%d): the slower loop is used for that call" % (name, code))


class Loops:
    """fastloops with the library's loops in their place (any other name: fastloops' own). The first try hands
    the arrays over as they are (cheap); NEED_COPY = plain copies, once more."""

    def __init__(self, base, dll, ext=None):
        mine = {n for n in type(self).__dict__ if not n.startswith("_")}
        if base is not None:  # (fastloops' names copied in: a plain lookup, as quick as the module's own)
            self.__dict__.update((n, v) for n, v in vars(base).items() if not n.startswith("__") and n not in mine)
        self.base = base
        self.dll = dll
        if ext is not None:  # (the extension's functions take the same arguments as the ctypes ones)
            self._stir_f, self._winner_f, self._key_f = ext.stir_spots, ext.winner_pieces, ext.key_order
        else:
            self._stir_f, self._winner_f, self._key_f = dll.sw_stir_spots, dll.sw_winner_pieces, dll.sw_key_order

    def __getattr__(self, name):  # (a name fastloops got after the copy)
        return getattr(self.base, name)

    def _stir_again(self, got, x, y, fp, ip, ox, oy, many, fallback):
        """The first try didn't run (got < 0): plain copies once more, else the fastloops loop."""
        if got == NEED_COPY:
            x, y, fp, ip = _in(x, np.float64), _in(y, np.float64), _in(fp, np.float64), _in(ip, np.int64)
            wx, wy = _in(ox, np.float64).copy(), _in(oy, np.float64).copy()
            got = self._stir_f(x, y, fp, ip, wx, wy, many)
            if got >= 0:
                ox[...], oy[...] = wx, wy
        if got < 0:
            _failed_once("stir_spots", got)
            fallback(x, y, fp, ip, ox, oy)

    def stir_spots(self, x, y, fp, ip, ox, oy):
        """fastloops.stir_spots: on half the cores past CORES_LEAST spots."""
        got = self._stir_f(x, y, fp, ip, ox, oy, 1)
        if got < 0:
            self._stir_again(got, x, y, fp, ip, ox, oy, 1, self.base.stir_spots)

    def stir_spots_one(self, x, y, fp, ip, ox, oy):
        """fastloops.stir_spots_one: one core."""
        got = self._stir_f(x, y, fp, ip, ox, oy, 0)
        if got < 0:
            self._stir_again(got, x, y, fp, ip, ox, oy, 0, self.base.stir_spots_one)

    def winner_pieces(self, group, s, e, rank, order):
        """fastloops.winner_pieces -> (each piece's note, start, end: 2 places a note; how many)."""
        n2 = 2 * len(order)
        note, ps, pe = np.empty(n2, np.int64), np.empty(n2, np.int64), np.empty(n2, np.int64)
        m = self._winner_f(group, s, e, rank, order, note, ps, pe)
        if m == NEED_COPY:
            group, s, e, rank, order = (_in(a, np.int64) for a in (group, s, e, rank, order))
            m = self._winner_f(group, s, e, rank, order, note, ps, pe)
        if m < 0:
            _failed_once("winner_pieces", m)
            return self.base.winner_pieces(group, s, e, rank, order)
        return note, ps, pe, m

    def _key_again(self, got, key, a, b, c, out, many, fallback):
        """The first try didn't run (got < 0): plain copies once more, else the fastloops loop."""
        if got == NEED_COPY:
            key, a, b, c = (_in(v, np.int64) for v in (key, a, b, c))
            got = self._key_f(key, a, b, c, out, many)
        if got < 0:
            _failed_once("key_order", got)
            return fallback(key, a, b, c)
        return out

    def key_order(self, key, a, b, c):
        """fastloops.key_order: np.lexsort((c, b, a, key)), one core."""
        out = np.empty(len(key), np.int64)
        got = self._key_f(key, a, b, c, out, 0)
        return out if got >= 0 else self._key_again(got, key, a, b, c, out, 0, self.base.key_order)

    def key_order_cores(self, key, a, b, c):
        """fastloops.key_order_cores: the key groups on half the cores past CORES_LEAST rows."""
        out = np.empty(len(key), np.int64)
        got = self._key_f(key, a, b, c, out, 1)
        return out if got >= 0 else self._key_again(got, key, a, b, c, out, 1, self.base.key_order_cores)
