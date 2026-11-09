"""Speed-ups: our own compiled loops (files/native.py, a library built from native/loops in Rust; shipped with
Spiderweb) and Pillow (optional: pictures to the screen in half the time, more picture file types). Spiderweb works
the same without them, just slower: the NumPy / Tk code they replace is kept and used then.

start() loads the compiled loops in the background; loops() is None until they're ready, then native.Loops (each
loop's plain-Python twin in files/fastloops.py is the last resort of a call the library refuses). No library here
(a Mac, an older Linux) but Numba installed (from source): Numba's compiled fastloops instead (files/numba_loops.py,
user 2026-11-09); neither = None, the NumPy code. SPIDERWEB_SPEED
in the environment: "off" = never use them, "now" = load them at once instead of in the background (tests: the same
code every run). (Until 2026-11 the loops were Numba's; PLAN.md B.)"""

import os
import threading
import tkinter as tk

_loops = None
_started = False
_failed = False
_own_tried = threading.Event()  # set once our own library loaded or couldn't (Numba's slow compile not waited for)
OWN_WAIT_S = 3.0  # wait_own's longest wait: a library that hangs never holds the start up longer


def start():
    """Starts loading the compiled loops (only the first call does anything)."""
    global _started
    if _started:
        return
    _started = True
    how = os.environ.get("SPIDERWEB_SPEED", "")
    if how == "off":
        global _failed
        _failed = True
        _own_tried.set()
        return
    if how == "now":
        _load()
    else:
        threading.Thread(target=_load, daemon=True, name="compiled loops").start()


def _load():
    global _loops, _failed
    import sys
    import time
    from files import errors, native
    began = time.perf_counter()
    try:
        _loops = native.over()
        how = ("compiled loops ready in %.1f s" % (time.perf_counter() - began)) if _loops else "compiled loops off"
    except Exception:  # noqa: BLE001 (the library there but failing: the NumPy code is used, worth a line in the logs)
        _loops = None
        how = "compiled loops off: " + repr(sys.exc_info()[1])
        errors.write_log(*sys.exc_info(), "(loading the compiled loops; the slower code is used)")
    finally:
        _own_tried.set()
    own =("on (%s)" % native.level_name()) if _loops is not None else "off: " + (native.why() or how)
    second = ""
    if _loops is None:  # (no own library here: Numba's loops when it's installed, else the NumPy code)
        _loops, second = _numba_loops()
        if _loops is not None:
            how = "Numba's compiled loops ready in %.1f s" % (time.perf_counter() - began)
    if _loops is None:
        _failed = True
    errors.note("speed-ups", f"own loops {own}{second}, Pillow {'on' if pillow() else 'off'}")
    report = os.environ.get("SPIDERWEB_SPEED_REPORT")  # (the exe test asks how it went)
    if report:
        with open(report, "w", encoding="utf-8") as f:
            f.write(how + "\nOwn loops: " + own + "\nPillow: " + ("yes" if pillow() else "no") + "\n")
    _drop_numba_saved()


def _numba_loops():
    """(Numba's compiled loops or None, the log's words for it): the second choice when our own library can't load
    (files/numba_loops.py: from source with Numba installed, e.g. a Mac or an older Linux)."""
    import sys
    from files import errors, numba_loops
    if not numba_loops.available():
        return None, ", NumPy"
    try:
        m = numba_loops.load()
    except Exception:  # noqa: BLE001 (Numba there but failing here: the NumPy code, worth a line in the logs)
        errors.write_log(*sys.exc_info(), "(compiling Numba's loops; the slower code is used)")
        return None, ", Numba failed, NumPy"
    return (m, ", Numba on") if m is not None else (None, ", NumPy")


def _drop_numba_saved():
    """The exe's user/compiled-loops folder: Numba's saved loops from versions before 2026-11 (tens of MB), never
    used again. Deleted once; only our own folder, nothing else."""
    import sys
    if not getattr(sys, "frozen", False):
        return
    from files import about
    folder = os.path.join(about.USER, "compiled-loops")
    if os.path.isdir(folder):
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


def wait_own(timeout=OWN_WAIT_S):
    """Waits (at most timeout s) until our own library has loaded or failed: the start's autosave opens with the
    compiled loops instead of the NumPy code (a project with a Warp took ~7 s to open at start instead of ~1 s;
    the library loads in ~0.06 s). Numba's compiling (a Mac) is never waited for."""
    if _started:
        _own_tried.wait(timeout)


def loops():
    """The loops once they're ready (native.Loops, or Numba's compiled fastloops), else None (the NumPy code)."""
    return _loops


def compiled(name):
    """loops() when its loop name is really compiled (the library has it, or Numba's), else None: the NumPy code
    then (a library older than the loop would hand it to the plain-Python twin, far slower than NumPy)."""
    fast = _loops
    if fast is None:
        return None
    x = getattr(fast, "x", False)  # (native.Loops: its extension module; Numba's module has none)
    if x is False:
        return fast
    from files.native_wraps import NEEDS
    return fast if x is not None and all(hasattr(x, n) for n in NEEDS.get(name, (name,))) else None


def key_sort(key, *cols):
    """np.lexsort((..., cols[1], cols[0], key)): rows by key, then each column in turn, rows alike in their order.
    The compiled loop for whole-number columns and a small key range (keys of a piano roll), else NumPy's."""
    import numpy as np
    fast = _loops
    n = len(key)
    if (fast and 0 < len(cols) <= 3 and n > 1 and all(getattr(c, "dtype", None) == np.int64 for c in (key, *cols))
            and int(key.max()) - int(key.min()) <= max(65536, n)):
        a = [np.ascontiguousarray(c) for c in cols]
        a += [a[-1]] * (3 - len(a))  # (the last column again: changes no order)
        return fast.key_order_cores(np.ascontiguousarray(key), *a)
    return np.lexsort((*cols[::-1], key))


def key_rows(keys):
    """(key, rows) for each key in keys, lowest first; rows as np.flatnonzero(keys == key)."""
    import numpy as np
    keys = np.asarray(keys)
    if not len(keys):
        return []
    order = key_sort(keys, keys) if keys.dtype == np.int64 else np.argsort(keys, kind="stable")
    sk = keys[order]
    cut = np.concatenate([[0], np.flatnonzero(sk[1:] != sk[:-1]) + 1, [len(sk)]])
    return [(sk[a], order[a:b]) for a, b in zip(cut[:-1].tolist(), cut[1:].tolist())]


def without_loops():
    """True = the compiled loops won't come (no library, it failed, or turned off); False while they still load."""
    return _failed or not _started


# ------------------------------------------------------------------ Pillow

def pillow():
    """(Image, ImageTk) or None when Pillow isn't installed."""
    try:
        from PIL import Image, ImageTk
    except ImportError:
        return None
    return Image, ImageTk


class Photo:
    """A Tk picture filled from NumPy arrays (rows x columns x 3, uint8 RGB). photo = the plain tk.PhotoImage (for
    the canvas, "copy", reading pixels). A whole picture goes in through Pillow when it's there (about half the
    time of PPM bytes at a piano roll's size), parts as PPM bytes. Keep the Photo while its photo is shown: with
    Pillow the Tk picture is deleted when the Photo goes (at once: an old picture held on to by the Tk one, a loop
    only Python's clean-up pass freed, piled up ~200 MB while the window was resized)."""

    def __init__(self, master, width, height):
        self.size = (width, height)
        self._pil = None  # (Pillow's picture: it must be kept, its photo is deleted with it)
        self._fast = False
        got = pillow()
        if got:
            try:
                self._pil = got[1].PhotoImage("RGB", (width, height), master=master)
                self.photo = self._pil._PhotoImage__photo  # (Pillow keeps its tk.PhotoImage here)
                self._fast = isinstance(self.photo, tk.PhotoImage)
            except Exception:
                self._fast = False
        if not self._fast:
            self.photo = tk.PhotoImage(master=master, width=width, height=height)

    def whole_is_quicker(self, pixels):
        """Is sending the whole picture quicker than that many pixels as a part? (Measured at a piano roll's size:
        the whole one through Pillow ~4 ms, a part ~20 ns a pixel.)"""
        return self._fast and pixels * 7 > self.size[0] * self.size[1]

    def put(self, a, x=0, y=0):
        """a's pixels at (x, y) of the picture."""
        if self._fast and x == 0 and y == 0 and (a.shape[1], a.shape[0]) == self.size:
            try:
                Image = pillow()[0]
                self._pil.paste(Image.fromarray(a if a.flags.c_contiguous else a.copy(), "RGB"))
                return
            except Exception:  # (Pillow can't reach this Tk: PPM from now on)
                self._fast = False
        self.photo.tk.call(self.photo.name, "put", b"P6 %d %d 255\n" % (a.shape[1], a.shape[0]) + a.tobytes(),
                           "-format", "ppm", "-to", x, y)
