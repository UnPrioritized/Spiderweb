"""Our own compiled loops: a library built from native/loops (Rust), loaded by ctypes like BASS (PLAN.md B: it
replaced the Numba loops, 2026-11). Each gives exactly what the NumPy code it stands in for gives (dev/tests
native_loops / native_replay / fast_loops compare them). No library (not built, another system, a 32-bit Python,
SPIDERWEB_NATIVE=off) = speed.loops() None: the NumPy code is used. The plain-Python twins in files/fastloops.py
(the source the library copies) are only the last resort of a call the library refuses.

The library holds every loop four times (x86-64 levels: any, SSE4.2, AVX2, AVX-512) and picks the highest the
processor has (SPIDERWEB_NATIVE_LEVEL=1-4 forces one, for checks). CHEAP CALLS (PLAN.md B:
never slower than Numba, small inputs too): the same file is ALSO loaded as a Python extension module (pyext.rs:
a call ~0.4 us, ctypes' own floor is ~1 us, Numba's ~0.8 us; load_ext; it fails = the ctypes functions, the same
loops). Arrays are handed over as they are (the library reads NumPy's array struct, checked at load by probe()); one it can't read as it is (another type, spread out in memory, read
only) = NEED_COPY, made plain here and handed over again. A loop that fails inside (a bad index: caught there, it
never ends the program; no memory) returns below 0 and that call is done by Numba's compiled twin when Numba is
installed (files/numba_loops.backup, that loop made on its first call), else by the fastloops twin (plain Python:
slow); noted once in the logs. Never asked about (user 2026-11-09).
  stir_spots, stir_spots_one   recolour.stir_from's middle (stir_spots on half the cores past CORES_LEAST spots)
  winner_pieces                engine.winner_stretches' pieces (Remove overlaps, split around)
  key_order, key_order_cores   np.lexsort by key, then 3 more columns (key_order_cores: the key groups on half
                               the cores past CORES_LEAST rows)
  the rest: files/native_wraps.py (NEEDS lists them; extension calls only: pyext.rs py_loops!)"""

import ctypes
import os
import platform
import struct
import sys

import numpy as np

from files.about import HERE
from files.native_wraps import NEEDS, Wraps
from files.system import MAC, WINDOWS, WINE

ABI = 5  # (the library's sw_abi(): another one = not used)
# The library's element kinds (lib.rs K_*, the same order): (NumPy kind letter, bytes, the dtype copies are made in)
KIND_TYPES = [("i", 8, np.int64), ("f", 8, np.float64), ("u", 1, np.uint8), ("i", 1, np.int8), ("i", 2, np.int16),
              ("u", 2, np.uint16), ("i", 4, np.int32), ("u", 4, np.uint32), ("u", 8, np.uint64), ("f", 4, np.float32),
              ("b", 1, np.bool_)]
RUST_TYPES = {"i64": 0, "f64": 1, "u8": 2, "i8": 3, "i16": 4, "u16": 5, "i32": 6, "u32": 7, "u64": 8, "f32": 9,
              "Flag": 10}  # (the type names in the extension's docs -> KIND_TYPES)


def kind_masks():
    """Each element kind's NumPy type numbers, a bit each (int64 can be "long" and "long long", ...)."""
    nums = {}
    for code in "?bBhHiIlLqQefdg":
        t = np.dtype(code)
        nums.setdefault((t.kind, t.itemsize), set()).add(t.num)
    return [sum(1 << n for n in nums.get((k, size), ()) if n < 63) for k, size, _ in KIND_TYPES]
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
                           ("sw_kinds", [ctypes.c_void_p, i64]),
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
        masks = np.array(kind_masks(), np.int64)
        d.sw_kinds(masks.ctypes.data, len(masks))
        force = os.environ.get("SPIDERWEB_NATIVE_LEVEL", "")  # (checks: 1-4 = that level, if this processor has it)
        if force.isdigit():
            d.sw_set_level(int(force))
        if WINE and not os.environ.get("SPIDERWEB_LOOPS_CHECK"):
            level = checked_level(d.sw_level(), path)
            if not level:
                _why = "failed its check on this system"
                return None
            d.sw_set_level(level)
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


CHECK_WAIT_S = 180  # (checked_level: one level's run taking longer = the system is slow, not the level wrong)


def checked_level(top, path):
    """(Wine-like systems only; untested on a real emulator) The processor there may be emulated, and one claiming
    AVX2 / AVX-512 it can't really run would end the program at the first loop. So the level is first tried in a
    separate run of Spiderweb (spiderweb.py --loops-check: a few loops on big inputs, answers compared with NumPy's),
    from the highest the processor names down to plain; 0 = none worked (the NumPy code). Remembered in
    user/loops-level.json per system version + processor + library, so it's tried once (a run past CHECK_WAIT_S:
    NumPy for now, tried again next start)."""
    import json
    import subprocess
    from files import about, safefile
    from files.system import cpu_name
    saved = os.path.join(about.USER, "loops-level.json")
    key = f"{WINE}|{cpu_name()}|{os.path.getsize(path)}|{ABI}|{top}"
    try:
        with open(saved, encoding="utf-8") as f:
            known = json.load(f)
        if not isinstance(known, dict):
            known = {}
    except (OSError, ValueError):
        known = {}
    if isinstance(known.get(key), int):
        return known[key]
    start = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable,
                                                                     os.path.join(HERE, "spiderweb.py")]
    level = 0
    for tried in range(top, 0, -1):
        env = dict(os.environ, SPIDERWEB_LOOPS_CHECK="1", SPIDERWEB_NATIVE_LEVEL=str(tried))
        try:
            done = subprocess.run(start + ["--loops-check"], env=env, timeout=CHECK_WAIT_S,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if done.returncode == 0:
                level = tried
                break
        except subprocess.TimeoutExpired:  # (a slow system, not the level's fault: the NumPy code this run, not
            return 0                       # remembered, so the next start tries again)
        except (OSError, subprocess.SubprocessError):
            pass
    known[key] = level
    try:
        safefile.write_text(saved, json.dumps(known))
    except OSError:
        pass
    return level


def self_check():
    """(spiderweb.py --loops-check, a run of its own) A few loops at SPIDERWEB_NATIVE_LEVEL on inputs big enough for
    their fastest code, answers compared with NumPy's. Exit code: 0 = fine, else not (a crash ends it anyway)."""
    d = load()
    if d is None:
        return 3
    loops = over()
    if loops is None:
        return 3
    g = np.random.default_rng(7)
    n = CORES_LEAST * 4
    key, a, b, c = (g.integers(0, k, n).astype(np.int64) for k in (128, 50, 1000, 1 << 40))
    want = np.lexsort((c, b, a, key))
    for f in (loops.key_order, loops.key_order_cores):
        if not np.array_equal(f(key, a, b, c), want):
            return 4
    return 0


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
        if m.setup_arrays(np.empty, *(np.dtype(t) for _, _, t in KIND_TYPES)) != 0:  # (loops handing back arrays)
            return None
        return m
    except Exception:  # noqa: BLE001 (any failure: the ctypes calls)
        return None


def why():
    """Why there's no library ("" when there is one)."""
    return "" if _dll and _ext else (_why or "not loaded yet")


def level_name():
    """"AVX-512" / "AVX2" / "SSE4.2" / "plain": the builds the library uses (the logs' state line)."""
    return (LEVELS.get(_dll.sw_level(), "plain") + ("" if _ext else ", ctypes calls")) if _dll else "plain"


def over():
    """The loops the program uses (Loops, fastloops' plain twins behind them); None without the library, and None
    when only its ctypes part loaded (the extension calls failing): most loops exist only as extension calls, so
    Loops would put the slow plain twins in their place; Numba's loops / the NumPy code are far quicker."""
    global _why
    d = load()
    if not d:
        return None
    if _ext is None:
        _why = "its extension calls didn't load"
        return None
    from files import fastloops
    return Loops(fastloops, d, _ext)


def _in(a, dtype):
    """a as the library reads it: 1-D, side by side, of that type (a copy only when it isn't)."""
    return np.ascontiguousarray(a, dtype)


def _failed_once(name, code):
    if name not in _failed:
        _failed.add(name)
        from files import errors
        errors.note("own loops", "%s refused a call (%d): its plain-Python twin did it (slower)" % (name, code))


def arg_kinds(f):
    """The extension function's arguments as its doc names them (py_loops!): per argument (None = a plain number /
    yes-no) or (dtype, writable) for an array."""
    out = []
    for t in (f.__doc__ or "").split(";"):
        t = t.strip()
        if not t:
            continue
        if t.startswith(("&", "Nd")):
            name = t.replace("&", "").replace("mut", "").replace("[", " ").replace("]", " ").replace("<", " ")
            name = name.replace(",", " ").split()
            word = name[1] if name[0] in ("Nd", "NdMut") else name[0]
            out.append((KIND_TYPES[RUST_TYPES[word]][2], "mut" in t or "NdMut" in t))
        else:
            out.append(t)  # ("i64", "f64", "bool")
    return out


_kinds = {}


def plain_call(f, args):
    """f once more with every array as the library reads it (its own kind, side by side, writable when written):
    copies made only of the ones that aren't; what the loop wrote copied back. Another kind that can't hold every
    value safely (e.g. int64 for int16) = NEED_COPY (the slower loop)."""
    kinds = _kinds.get(f)
    if kinds is None:
        kinds = _kinds[f] = arg_kinds(f)
    if len(kinds) != len(args):
        return NEED_COPY
    plain = []
    for a, k in zip(args, kinds):
        if isinstance(k, str):
            if isinstance(a, np.ndarray) and a.ndim == 0:
                a = a.item()
            if k in ("i64", "u64"):
                if not isinstance(a, (int, np.integer, bool, np.bool_)):
                    return NEED_COPY  # (a fraction where a whole number goes: the slower loop)
                a = int(a) & ((1 << 64) - 1) if k == "u64" else int(a)
            plain.append(float(a) if k == "f64" else bool(a) if k == "bool" else a)
            continue
        dtype, writes = k
        if not isinstance(a, (np.ndarray, list, tuple)):  # (a number / None where an array goes: refused, never
            return NEED_COPY                               # turned into a one-item array)
        a2 = np.asarray(a)
        if a2.dtype != dtype and not np.can_cast(a2.dtype, dtype, "safe"):
            return NEED_COPY
        p = np.ascontiguousarray(a2, dtype)
        if writes and (p is a2 and not p.flags.writeable):
            p = p.copy()
        plain.append(p)
    got = f(*plain)
    if got.__class__ is not int or got >= 0:  # (a tuple: a loop handing back arrays ran)
        for a, p, k in zip(args, plain, kinds):
            if not isinstance(k, str) and k[1] and p is not a and isinstance(a, np.ndarray):
                a[...] = p
    return got


class CFunc:
    """A plain C function of the library, as BASS calls it (synth.py: _DSPPROC(fast.limit_dsp.address))."""

    def __init__(self, address):
        self.address = address


_lim = {}


def limiter(dll):
    """The library's limiter for BASS (sw_limit_dsp) once synth.py's numbers are handed over and match the layout
    built into it; None = not there / another layout (synth.py's Python limiter then). Also readies
    dll.sw_limit_live (checks)."""
    if dll is None:
        return None
    if dll not in _lim:
        got = None
        try:
            from files import synth as s
            nums = np.array([s.LIM_BLOCK, s.LIM_DELAY, s.LIM_RING, s.LIM_N, s.LIM_ON, s.LIM_MIX, s.LIM_ENV, s.LIM_G0,
                             s.LIM_G1, s.LIM_PEAK, s.LIM_PREV, s.LIM_HEAD, s.LIM_SIZE, s.LIM_SLOTS, s.LIM_TOP,
                             s.LIM_ALL, s.LIM_CEILING, s.LIM_FALL, s.LIM_STEP], np.float64)
            dll.sw_lim_setup.argtypes, dll.sw_lim_setup.restype = [ctypes.c_void_p, ctypes.c_int64], ctypes.c_int64
            v = ctypes.c_void_p
            dll.sw_limit_live.argtypes = [v, v, ctypes.c_int64, v, ctypes.c_int64]
            dll.sw_limit_live.restype = ctypes.c_int64
            if dll.sw_lim_setup(nums.ctypes.data, len(nums)) == 0:
                got = CFunc(ctypes.cast(dll.sw_limit_dsp, ctypes.c_void_p).value)
        except (AttributeError, ImportError, OSError):
            got = None
        _lim[dll] = got
    return _lim[dll]


_BYTES = np.dtype(np.uint8)


def _as_bytes(a):
    """A byte array as plain Python reads bytes (memoryview: each item a Python int, as the no-loops path hands
    them over); anything else as it is."""
    if isinstance(a, np.ndarray) and a.dtype == _BYTES and a.ndim == 1 and a.flags.c_contiguous:
        return memoryview(a)
    return a


class PlainTwins:
    """fastloops' plain-Python twins as a refused call uses them when Numba isn't there. They were written for
    Numba, which reads every array item as a 64-bit number; plain Python under NumPy 2 keeps a byte as a uint8
    (shifts overflow, Load MIDI could get stuck) and a small whole number as its own kind (sums past it fail). So
    the loops in BYTE_LOOPS get their byte arrays as memoryviews (as the no-loops path calls midi_scan / midi_index)
    and those in WIDE_LOOPS their small whole-number arrays read as int64 copies (only ones the loop never writes).
    Same answers as Numba's (test plain_twins_same); every other name is the twin as it is."""

    BYTE_LOOPS = frozenset(("midi_walk", "midi_rewrite", "midi_note_ticks", "midi_busy_hits", "midi_gather",
                            "midi_index", "midi_cover", "midi_cover_many"))
    WIDE_LOOPS = {"order_rects": {2}, "order_screen": {2}, "last_on_pixels": {2}}  # (name: places of read-only
    # small whole-number arrays: the notes' keys, int16)

    def __init__(self, base):
        self._base = base

    def __getattr__(self, name):
        f = getattr(self._base, name)
        if name in self.BYTE_LOOPS:
            def bytes_twin(*args):
                return f(*(_as_bytes(a) for a in args))
            return bytes_twin
        wide = self.WIDE_LOOPS.get(name)
        if wide:
            def wide_twin(*args):
                return f(*(np.asarray(a, np.int64) if k in wide and isinstance(a, np.ndarray) else a
                           for k, a in enumerate(args)))
            return wide_twin
        return f


class Loops(Wraps):
    """fastloops with the library's loops in their place (any other name: fastloops' own). The first try hands
    the arrays over as they are (cheap); NEED_COPY = plain copies, once more."""

    def __init__(self, base, dll, ext=None):
        mine = {n for c in type(self).__mro__ for n in c.__dict__ if not n.startswith("_")}
        if base is not None:  # (fastloops' names copied in: a plain lookup, as quick as the module's own)
            self.__dict__.update((n, v) for n, v in vars(base).items() if not n.startswith("__") and n not in mine)
        self._plain = PlainTwins(base) if base is not None else None
        for name, needs in NEEDS.items():  # (a loop the library hasn't got: fastloops' own)
            if ext is None or not all(hasattr(ext, x) for x in needs):
                if base is not None and hasattr(base, name):
                    self.__dict__[name] = getattr(self._plain, name)
        self.dll = dll
        self.x = ext
        if ext is not None:  # (the extension's functions take the same arguments as the ctypes ones)
            self._stir_f, self._winner_f, self._key_f = ext.stir_spots, ext.winner_pieces, ext.key_order
        else:
            self._stir_f, self._winner_f, self._key_f = dll.sw_stir_spots, dll.sw_winner_pieces, dll.sw_key_order
        # (Built-in BASSMIDI's limiter: BASS's sound thread calls the library's C function; None = synth.py's own)
        self.__dict__["limit_dsp"] = limiter(dll)

    @property
    def base(self):
        """Where a refused call goes (read only then; user 2026-11-09, never asked): Numba's compiled fastloops when
        Numba is installed (files/numba_loops.backup: only that loop made, on its first call), else the plain
        twins."""
        from files import numba_loops
        return numba_loops.backup() or self._plain

    @base.setter
    def base(self, module):  # (checks put a traced stand-in for the plain twins here)
        self._plain = module

    def __getattr__(self, name):  # (a name fastloops got after the copy)
        if name == "_plain":
            raise AttributeError(name)
        return getattr(self._plain, name)

    def _again(self, name, got, args):
        """The extension's loop name didn't run (got < 0): NEED_COPY = plain copies once more. -> what it gave
        (still < 0 = said once in the logs; the caller uses the fastloops loop)."""
        if got == NEED_COPY:
            got = plain_call(getattr(self.x, name), args)
        if got.__class__ is int and got < 0:
            _failed_once(name, got)
        return got

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
