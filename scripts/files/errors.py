"""Errors: the details go to spiderweb-error.log and the user is told once per session; a crash that ends the program
goes to spiderweb-crash.log. Both sit right next to spiderweb.py / Spiderweb.exe (not in user/: the user should find
them easily), or in user/ when that folder can't be written to. The .exe and pythonw have no console, so without
this an error would just be invisible."""

import os
import platform
import re
import sys
import threading
import time
import traceback

from files.lang import tr
from files.about import HERE, USER, VERSION
from files.system import error_box

LOG = os.path.join(HERE, "spiderweb-error.log")
OLD_LOG = os.path.join(HERE, "spiderweb-error-old.log")  # the log moves here when it gets big
MAX_LOG = 1_000_000  # bytes
CRASH_NOW = os.path.join(USER, "crash-now.log")  # this run's: faulthandler writes here if Spiderweb crashes
CRASH_LOG = os.path.join(HERE, "spiderweb-crash.log")  # every crash so far (crash-now.log moved in on the next start)
OLD_CRASH_LOG = os.path.join(HERE, "spiderweb-crash-old.log")
OLD_NAMES = ((os.path.join(USER, "errors.log"), LOG), (os.path.join(USER, "errors-old.log"), OLD_LOG))

_app = None
_crash_file = None  # (kept open the whole run: faulthandler writes into it)  # the window, once there is one
_told = False
_told_memory = False  # (out of memory said once too, apart from other errors)
_seen = {}  # error text -> times it happened this session (an error on every mouse move would flood the log)
# What this PC and session use (PC, screen, MIDI out, soundfont, Hz preview...): topic -> text. Each change is a
# line in the crash log at once (a crash can't write anything more), and the error log's next entry gets the lines
# changed since its last one. Lines start with NOTE so the next start can tell them from a crash's own lines.
NOTE = "-- "
_state = {}
_state_logged = {}  # what the error log was last told
_state_lock = threading.Lock()


def note(topic, text):
    """Remembers what's in use now (e.g. note("soundfont", "Piano.sf2, 340 MB")): the logs show it with a crash /
    an error. File names only, never folders (their names can hold the user's name, and logs get shared)."""
    with _state_lock:
        if _state.get(topic) == text:
            return
        _state[topic] = text
        if _crash_file is not None:
            try:
                _crash_file.write(f"{NOTE}{time.strftime('%H:%M:%S')} {topic}: {text}\n")
                _crash_file.flush()
            except (OSError, ValueError):
                pass


def _state_lines():
    """The state lines the error log hasn't been told yet."""
    with _state_lock:
        changed = {k: v for k, v in _state.items() if _state_logged.get(k) != v}
        _state_logged.update(changed)
    return "".join(f"{NOTE}{k}: {v}\n" for k, v in changed.items())


def pc_text():
    """The PC in one line: processor, cores, memory, exe or source."""
    from files.system import cpu_name, free_memory, total_memory
    gb = lambda n: "?" if n is None else f"{n / 2 ** 30:.1f} GB"
    return (f"{cpu_name() or 'processor ?'}, {os.cpu_count() or '?'} cores, {gb(total_memory())} memory "
            f"({gb(free_memory())} free), {'exe' if getattr(sys, 'frozen', False) else 'source'}")


def _folder_rules():
    """(pattern, short name) for the folders hidden in the logs, longest first: Spiderweb's own (and the exe's
    unpacked files) -> <Spiderweb>, the user's home folder -> ~."""
    folders = [(HERE, "<Spiderweb>"), (getattr(sys, "_MEIPASS", ""), "<Spiderweb>"), (os.path.expanduser("~"), "~")]
    rules = []
    for folder, name in sorted(folders, key=lambda f: -len(f[0])):
        folder = os.path.normpath(folder).rstrip("\\/") if folder else ""
        if len(folder) < 4:  # (not there, a drive's root, or "~" not found)
            continue
        # (an error's message quotes paths with doubled backslashes: 'C:\\Users\\...')
        pattern = r"[\\/]+".join(re.escape(part) for part in re.split(r"[\\/]", folder))
        rules.append((re.compile(pattern + r"(?=[\\/\s\"',;)\]]|$)", re.IGNORECASE if os.name == "nt" else 0),
                      name.replace("\\", "\\\\")))
    return rules


_FOLDERS = _folder_rules()


def hide_folders(text):
    """Logs get shared and folder names can hold the user's name: Spiderweb's folder becomes <Spiderweb>, the home
    folder ~ (file names stay)."""
    for pattern, name in _FOLDERS:
        text = pattern.sub(name, text)
    return text


def write_log(exc_type, exc, tb, where):
    """Adds the error to errors.log. True if it could be written."""
    text = hide_folders("".join(traceback.format_exception(exc_type, exc, tb)))
    head = (f"==== {time.strftime('%Y-%m-%d %H:%M:%S')}   Spiderweb {VERSION}   Python {platform.python_version()}"
            f"   {platform.platform()}   {where}")
    times = _seen[text] = _seen.get(text, 0) + 1
    if times == 2:  # the full details are already in the log once
        text = (f"The same {exc_type.__name__} as logged earlier this session happened again; "
                "further repeats aren't logged.\n")
    elif times > 2:
        return True
    lines = _state_lines()
    if lines:
        head += "\n" + lines.rstrip("\n")
    if sys.stderr:  # started from a console: show it there too
        print(head, text, sep="\n", file=sys.stderr)
    return append(LOG, OLD_LOG, f"{head}\n{text}\n")


def append(path, old, text):
    """Adds text to a log (moved to old first when it's big; the folder can't be written to: the one in USER).
    True if it could be written."""
    global LOG, OLD_LOG, CRASH_LOG, OLD_CRASH_LOG
    for p, o in ((path, old), (os.path.join(USER, os.path.basename(path)), os.path.join(USER, os.path.basename(old)))):
        try:
            if os.path.getsize(p) > MAX_LOG:
                os.replace(p, o)
        except OSError:
            pass
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            continue
        if path == LOG:  # (the message names where it went)
            LOG, OLD_LOG = p, o
        elif path == CRASH_LOG:
            CRASH_LOG, OLD_CRASH_LOG = p, o
        return True
    return False


def move_old_logs():
    """errors.log in user/ (older versions) becomes spiderweb-error.log next to Spiderweb, once."""
    for old, new in OLD_NAMES:
        if os.path.exists(old) and not os.path.exists(new):
            try:
                os.replace(old, new)
            except OSError:
                pass


def tell_user(saved, fatal=False, memory=False):
    global _told, _told_memory
    if memory and not fatal:  # (out of memory: said in plain words, once, apart from other errors)
        if _told_memory:
            return
        _told_memory = True
    elif _told:
        return
    else:
        _told = True
    where = (tr("errors.the_details_were_saved_to", LOG=LOG) if saved else
             tr("errors.the_details_couldn_t_be_saved", LOG=LOG))
    if memory and not fatal:
        msg = tr("big_ask.out_of_memory_somewhere")
    elif fatal:
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
    error_box(tr("errors.spiderweb"), msg)  # (works without a Spiderweb window)


def report(exc_type, exc, tb, where="", fatal=False):
    saved = write_log(exc_type, exc, tb, where)
    memory = isinstance(exc_type, type) and issubclass(exc_type, MemoryError)
    if threading.current_thread() is not threading.main_thread() and _app is not None:
        try:
            _app.after(0, lambda: tell_user(saved, memory=memory))  # Tk windows belong to the main thread
        except Exception:
            pass
        return
    tell_user(saved, fatal, memory)


def watch_crashes():
    """A crash inside a DLL or compiled code (BASS, Numba, Tk) ends the program before Python can log anything.
    faulthandler then writes what every thread was doing into user/crash-now.log; the next start moves that into
    spiderweb-crash.log. A run that ended normally leaves only its start line and note lines (what was in use), which
    the next start drops."""
    global _crash_file
    import faulthandler
    try:
        with open(CRASH_NOW, encoding="utf-8", errors="replace") as f:
            last = f.read().strip()
    except OSError:
        last = ""
    crashed = any(line.strip() and not line.startswith(("==== ", NOTE)) for line in last.splitlines())
    if crashed and not append(CRASH_LOG, OLD_CRASH_LOG, hide_folders(last) + "\n\n"):
        return  # (crash-now.log kept for the next start rather than lost)
    try:
        _crash_file = open(CRASH_NOW, "w", encoding="utf-8")
        _crash_file.write(f"==== started {time.strftime('%Y-%m-%d %H:%M:%S')}   Spiderweb {VERSION}   Python "
                          f"{platform.python_version()} {platform.architecture()[0]}   {platform.platform()}\n")
        _crash_file.flush()
        faulthandler.enable(_crash_file, all_threads=True)
    except (OSError, ValueError, RuntimeError):
        pass
    try:
        note("PC", pc_text())
    except Exception:  # (only a log line: never stops Spiderweb from starting)
        pass


def install(app):
    """Catch errors in the window's callbacks (clicks, keys, timers) and in other threads (playback)."""
    global _app
    _app = app
    try:  # (screen size in pixels, scaling as Windows' "%": 96 dots an inch = 100 %)
        note("screen", f"{app.winfo_screenwidth()}x{app.winfo_screenheight()}, "
                       f"{round(app.winfo_fpixels('1i') / 96 * 100)} %")
    except Exception:
        pass
    app.report_callback_exception = lambda t, e, tb: report(t, e, tb, "(in the window)")
    threading.excepthook = lambda a: report(a.exc_type, a.exc_value, a.exc_traceback,
                                           f"(in thread {a.thread.name if a.thread else '?'})")
