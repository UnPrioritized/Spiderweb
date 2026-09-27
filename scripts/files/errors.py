"""Errors: the details go to errors.log (next to spiderweb.py / Spiderweb.exe) and the user is told once per session.
The .exe and pythonw have no console, so without this an error would just be invisible."""

import ctypes
import os
import platform
import sys
import threading
import time
import traceback

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
    where = f"The details were saved to:\n{LOG}" if saved else f"(The details couldn't be saved to {LOG}.)"
    if fatal:
        msg = f"Spiderweb couldn't start because of an error.\n\n{where}\n\nPlease include errors.log when you report it."
    else:
        msg = (f"Something went wrong in Spiderweb.\n\n{where}\n\n"
               "You can carry on. If your project looks wrong now, autosave-backup.json (in the same folder) holds "
               "your work as it was when you started Spiderweb; open it with Open project.\n\n"
               "Please include errors.log when you report the problem. (This message only shows once; later "
               "errors are saved to the log too.)")
    if _app is not None and not fatal:
        from tkinter import messagebox
        try:
            messagebox.showerror("Spiderweb", msg, parent=_app)
            return
        except Exception:
            pass
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, "Spiderweb", 0x10)  # works without a Tk window
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
