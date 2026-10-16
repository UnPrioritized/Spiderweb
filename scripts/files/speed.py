"""Optional speed-ups (user, 2026-10-06): Numba (compiled loops, files/fastloops.py) and Pillow (pictures to the
screen in half the time, more picture file types). Spiderweb works the same without them, just slower: the NumPy /
Tk code they replace is kept and used then.

start() loads the compiled loops in the background (the first start compiles them: a few seconds); loops() is
None until they're ready, then the fastloops module. SPIDERWEB_SPEED in the environment: "off" = never use them,
"now" = load them at once instead of in the background (tests: the same code every run)."""

import os
import threading
import tkinter as tk

_loops = None
_started = False
_failed = False
save_loops = True  # False = the saved loops failed this start: made again without saving (fastloops reads it)
_build = None


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
        return
    if how == "now":
        _load()
    else:
        threading.Thread(target=_load, daemon=True, name="compiled loops").start()


def _load():
    global _loops
    import importlib.util
    import sys
    import time
    began = time.perf_counter()
    try:
        _loops = _warm_loops()
        how = "compiled loops ready in %.1f s" % (time.perf_counter() - began)
    except Exception:  # no Numba (or it can't compile here): the NumPy code is used
        global _failed
        _failed = True
        how = "compiled loops off: " + repr(sys.exc_info()[1])
        if importlib.util.find_spec("numba") is not None:  # (there but not working: worth a line in errors.log)
            from files import errors
            errors.write_log(*sys.exc_info(), "(loading the compiled loops; the slower code is used)")
    from files import errors
    errors.note("speed-ups", f"Numba {'on' if _loops else 'off'}, Pillow {'on' if pillow() else 'off'}")
    report = os.environ.get("SPIDERWEB_SPEED_REPORT")  # (the exe test asks how it went)
    if report:
        with open(report, "w", encoding="utf-8") as f:
            f.write(how + "\nPillow: " + ("yes" if pillow() else "no") + "\n")


def _warm_loops():
    """fastloops, compiled (or loaded). Saved loops that fail (a damaged file; one that can't be saved: disk full,
    path too long) are thrown away and the loops made again without saving; the next start saves them anew."""
    global save_loops
    import importlib
    import importlib.util
    import sys
    try:
        from files import fastloops
        fastloops.warm()
        return fastloops
    except Exception:
        if not save_loops or importlib.util.find_spec("numba") is None:
            raise
        first = sys.exc_info()[1]
    from files import errors
    errors.note("compiled loops", "saved ones failed, made again: " + repr(first)[:200])
    save_loops = False
    _forget_saved()
    sys.modules.pop("files.fastloops", None)
    fastloops = importlib.import_module("files.fastloops")
    fastloops.warm()
    return fastloops


def loops_folder():
    """Where the saved compiled loops are: the exe's user/compiled-loops/<version>-<the exe's fingerprint>, from
    the source scripts/files/__pycache__ (Numba's own place)."""
    global _build
    import sys
    if not getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
    from files import about
    if _build is None:
        import hashlib
        h = hashlib.sha256()
        with open(sys.executable, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _build = "%s-%s" % (about.VERSION, h.hexdigest()[:10])
    return os.path.join(about.USER, "compiled-loops", _build)


def _forget_saved():
    """Deletes the saved compiled loops (this build's only)."""
    folder = loops_folder()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.startswith("fastloops.") and name.endswith((".nbi", ".nbc")):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def loops():
    """The fastloops module once it's ready, else None (use the NumPy code)."""
    return _loops


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
        return fast.key_order(np.ascontiguousarray(key), *a)
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
    """True = the compiled loops won't come (no Numba, it failed, or turned off); False while they still load."""
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
