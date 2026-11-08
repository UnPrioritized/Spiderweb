"""Numba as the second choice for the compiled loops (user 2026-11-09): when our own library can't load (a Mac, an
older Linux, a system it isn't built for) and Numba is installed (from source only: the exe never has it),
files/fastloops.py is loaded once more as its own module, "files.numba_fastloops", with Numba's real marks in
place of loop_marks' do-nothing ones, so its loops compile (as before 2026-11). Plus limit_dsp, the C callback
BASS's sound thread calls for Built-in BASSMIDI's limiter (Numba's cfunc around limit_live). The plain fastloops
module stays plain (the library's last resort). Compiled loops are saved beside fastloops.py (Numba's
__pycache__): only the first start compiles (a minute or so), in the background like the library's load.

    m = load()   # None when Numba isn't there (or can't load / compile here): the NumPy code is used
"""
import importlib.util
import os
import sys
import threading
import types

_STALE_PREFIXES = ("fastloops.", "midi_scan.", "midi_index.", "midi_play.", "midi_piece.", "synth.")


def available():
    """Numba installed here (only looked for, not loaded) and not the exe (SPIDERWEB_NUMBA=off: never, for checks)."""
    if getattr(sys, "frozen", False) or os.environ.get("SPIDERWEB_NUMBA") == "off":
        return False
    return importlib.util.find_spec("numba") is not None


def _folder():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")


def _drop_saved():
    """Deletes the saved compiled loops of fastloops and the files it compiles (made again)."""
    try:
        names = os.listdir(_folder())
    except OSError:
        return
    for name in names:
        if name.startswith(_STALE_PREFIXES) and name.endswith((".nbi", ".nbc")):
            try:
                os.remove(os.path.join(_folder(), name))
            except OSError:
                pass


def _drop_stale():
    """Numba checks only each function's own file, but synth.py's LIM_* numbers are built into the saved loops
    too: when they differ from the ones the saved loops were made with, those are deleted (made again)."""
    from files import safefile, synth
    now = repr(tuple(getattr(synth, n) for n in sorted(dir(synth)) if n.startswith("LIM_")))
    mark = os.path.join(_folder(), "fastloops-synth.txt")
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read() == now:
                return
    except OSError:
        pass
    try:
        os.makedirs(_folder(), exist_ok=True)
        _drop_saved()
        safefile.write_text(mark, now)
    except OSError:
        pass


def _compile(cache, warm=True):
    """fastloops compiled by Numba, as the module files.numba_fastloops. warm: every loop made now (+ limit_dsp,
    Numba's threads); else each loop is made on its first call (a refused call's backup: only that loop)."""
    import numba
    from numba import carray, cfunc
    from numba import types as nt
    from numba.typed import List
    marks = types.ModuleType("files.loop_marks")

    def njit(*args, **kw):
        kw.setdefault("cache", cache)
        if len(args) == 1 and callable(args[0]):
            return numba.njit(**kw)(args[0])
        return numba.njit(*args, **kw)

    def set_threads():
        numba.set_num_threads(max(1, min(numba.config.NUMBA_NUM_THREADS, (os.cpu_count() or 2) // 2)))

    marks.njit, marks.prange, marks.List, marks.set_threads = njit, numba.prange, List, set_threads
    from files import fastloops as plain
    was = sys.modules.get("files.loop_marks")
    sys.modules["files.loop_marks"] = marks
    try:
        spec = importlib.util.spec_from_file_location("files.numba_fastloops", plain.__file__)
        m = importlib.util.module_from_spec(spec)
        sys.modules["files.numba_fastloops"] = m
        spec.loader.exec_module(m)
    finally:
        if was is not None:
            sys.modules["files.loop_marks"] = was
        else:
            sys.modules.pop("files.loop_marks", None)
    if not warm:
        return m
    from files.synth import LIM_ALL, LIM_SIZE, LIM_SLOTS, LIM_TOP
    limit_live = m.limit_live

    # (BASS's DSPPROC: handle, channel = the stream, the piece, its bytes, user = synth._LIM_ALL: the stream's slot
    # found by its handle; none = left as made)
    @cfunc(nt.void(nt.uint32, nt.uint32, nt.CPointer(nt.float32), nt.uint32, nt.CPointer(nt.float64)), cache=cache)
    def limit_dsp(dsp, channel, buf, length, user):
        a = carray(user, (LIM_ALL,))
        for i in range(LIM_SLOTS):
            if a[i] == channel:
                limit_live(a[LIM_TOP + i * LIM_SIZE:LIM_TOP + (i + 1) * LIM_SIZE], carray(buf, (length // 4,)),
                           length // 8, a[LIM_SLOTS:LIM_TOP], i)
                return

    m.limit_dsp = limit_dsp
    m.warm()
    try:  # (Numba's threads started now, not on the first big Stir / Liquify: ~0.3 s once)
        import numpy as np
        m.set_threads()
        m._liquify_sample_cores(np.zeros((2, 2, 2)), np.full(4, 0.5), np.full(4, 0.5), np.zeros((4, 2)))
    except Exception:  # noqa: BLE001 (no threading layer here: on_cores falls back to one core by itself)
        pass
    return m


def load(warm=True):
    """The Numba-compiled loops, or None (no Numba, the exe, or it fails here: the NumPy code is used). Saved loops
    that fail (a damaged file, can't be written) are thrown away and the loops made again without saving.
    warm=False: nothing made yet (each loop on its first call)."""
    if not available():
        return None
    got = sys.modules.get("files.numba_fastloops")
    if got is not None and (not warm or getattr(got, "limit_dsp", None) is not None):
        return got
    try:
        import numba  # noqa: F401
    except Exception:  # noqa: BLE001 (installed but broken: the NumPy code)
        return None
    _drop_stale()
    try:
        return _compile(True, warm)
    except Exception:  # noqa: BLE001
        first = sys.exc_info()[1]
    from files import errors
    errors.note("compiled loops", "Numba's saved loops failed, made again: " + repr(first)[:200])
    sys.modules.pop("files.numba_fastloops", None)
    _drop_saved()
    return _compile(False, warm)


_backup = {}
_backup_lock = threading.Lock()


def backup():
    """For a call our own library refused (user 2026-11-09: never asked; Numba if installed, else the plain twin):
    the Numba module (loops made on their first call), or None. Looked up once."""
    if "m" not in _backup:
        with _backup_lock:
            if "m" not in _backup:
                try:
                    _backup["m"] = load(warm=False)
                except Exception:  # noqa: BLE001 (Numba failing here: the plain twin)
                    _backup["m"] = None
    return _backup["m"]
