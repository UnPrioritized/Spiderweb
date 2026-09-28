"""Errors: the details go to errors.log (next to spiderweb.py / Spiderweb.exe) and the user is told once per session.
The .exe and pythonw have no console, so without this an error would just be invisible."""

import ctypes
import os
import platform
import sys
import threading
import time
import traceback

from files.lang import tr
from files.about import HERE, VERSION

LOG = os.path.join(HERE, "errors.log")
OLD_LOG = os.path.join(HERE, "errors-old.log")  # errors.log moves here when it gets big
MAX_LOG = 1_000_000  # bytes

_app = None  # the window, once there is one
_told = False
_seen = {}  # error text -> times it happened this session (an error on every mouse move would flood the log)


def write_log(exc_type, exc, tb, where):
    """Adds the error to errors.log. True if it could be written."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    head = (f"==== {time.strftime('%Y-%m-%d %H:%M:%S')}   Spiderweb {VERSION}   Python {platform.python_version()}"
            f"   {platform.platform()}   {where}")
    times = _seen[text] = _seen.get(text, 0) + 1
    if times == 2:  # the full details are already in the log once
        text = (f"The same {exc_type.__name__} as logged earlier this session happened again; "
                "further repeats aren't logged.\n")
    elif times > 2:
        return True
    if sys.stderr:  # started from a console: show it there too
        print(head, text, sep="\n", file=sys.stderr)
    try:
        if os.path.getsize(LOG) > MAX_LOG:
            os.replace(LOG, OLD_LOG)
    except OSError:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{head}\n{text}\n")
        return True
    except OSError:
        return False


def tell_user(saved, fatal=False):
    global _told
    if _told:
        return
    _told = True
    where = (tr("errors.the_details_were_saved_to", LOG=LOG) if saved else
             tr("errors.the_details_couldn_t_be_saved", LOG=LOG))
    if fatal:
        msg = tr("errors.spiderweb_couldn_t_start_because_of", where=where)
    else:
        msg = (tr("errors.something_went_wrong_in_spiderweb_you", where=where))
    if _app is not None and not fatal:
        from tkinter import messagebox
        try:
            messagebox.showerror(tr("errors.spiderweb"), msg, parent=_app)
            return
        except Exception:
            pass
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, tr("errors.spiderweb"), 0x10)  # works without a Tk window
    except (AttributeError, OSError):
        pass


def report(exc_type, exc, tb, where="", fatal=False):
    saved = write_log(exc_type, exc, tb, where)
    if threading.current_thread() is not threading.main_thread() and _app is not None:
        try:
            _app.after(0, lambda: tell_user(saved))  # Tk windows belong to the main thread
        except Exception:
            pass
        return
    tell_user(saved, fatal)


def install(app):
    """Catch errors in the window's callbacks (clicks, keys, timers) and in other threads (playback)."""
    global _app
    _app = app
    app.report_callback_exception = lambda t, e, tb: report(t, e, tb, "(in the window)")
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback,
                                           f"(in thread {a.thread.name if a.thread else '?'})")
