"""Tk objects let go of on another thread (user's freeze 2026-11-19, merge / slice / undo "sometimes"): Python's
garbage collector runs in whatever thread happens to be making objects, so a Tk variable / image / font caught in
a reference cycle can be deleted inside the notes thread. Its __del__ calls Tk, which hands the call to Tk's thread
and waits; Tk's thread was waiting for the notes thread (App.notes_made's join) = both wait for ever. Here those
__del__s, on any thread but Tk's, only note the object down; Tk's thread lets it go at its next callback
(notes_wait._call -> tidy)."""

import collections
import threading
import tkinter as tk
from tkinter import font as tkfont

_main = threading.main_thread()
_owed = collections.deque()  # (the class's own __del__, the object): appended by other threads, emptied on Tk's


def _later(orig):
    def __del__(self):
        if threading.current_thread() is _main:
            orig(self)
        else:  # (kept alive until Tk's thread runs it: Python calls __del__ only once, so it's run by hand there)
            _owed.append((orig, self))
    __del__.__doc__ = orig.__doc__
    __del__._tk_threads = True
    return __del__


def install():
    """Called once by App (before its notes thread first runs)."""
    for cls in (tk.Variable, tk.Image, tkfont.Font):
        orig = cls.__dict__.get("__del__")
        if orig is not None and not getattr(orig, "_tk_threads", False):
            cls.__del__ = _later(orig)


def tidy():
    """On Tk's thread: the objects other threads let go of, deleted now."""
    while _owed:
        orig, obj = _owed.popleft()
        try:
            orig(obj)
        except Exception:  # noqa: BLE001 (window closed meanwhile: nothing left to delete in Tk)
            pass
