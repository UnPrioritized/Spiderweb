"""Optional speed-ups (user, 2026-10-06): Numba (compiled loops, files/fastloops.py) and Pillow (pictures to the
screen in half the time, more picture file types). Spiderweb works the same without them, just slower: the NumPy /
Tk code they replace is kept and used then.

start() loads the compiled loops in the background (the first start compiles them: about a second); loops() is
None until they're ready, then the fastloops module. SPIDERWEB_SPEED in the environment: "off" = never use them,
"now" = load them at once instead of in the background (tests: the same code every run)."""

import os
import threading
import tkinter as tk

_loops = None
_started = False


def start():
    """Starts loading the compiled loops (only the first call does anything)."""
    global _started
    if _started:
        return
    _started = True
    how = os.environ.get("SPIDERWEB_SPEED", "")
    if how == "off":
        return
    if how == "now":
        _load()
    else:
        threading.Thread(target=_load, daemon=True, name="compiled loops").start()


def _load():
    global _loops
    try:
        from files import fastloops
        fastloops.warm()
    except Exception:  # no Numba (or it can't compile here): the NumPy code is used
        return
    _loops = fastloops


def loops():
    """The fastloops module once it's ready, else None (use the NumPy code)."""
    return _loops


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
    time of PPM bytes at a piano roll's size), parts as PPM bytes."""

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
                self.photo.pillow_keep = self._pil  # (Pillow deletes the picture when its own goes: kept along)
            except Exception:
                self._fast = False
        if not self._fast:
            self.photo = tk.PhotoImage(master=master, width=width, height=height)

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
