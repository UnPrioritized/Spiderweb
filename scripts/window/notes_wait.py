"""Notes made in the background after an edit on a big project (App.shapes_changed, "late" notes): the window keeps
answering the view (scroll, zoom, pan, hover, the track list's scrolling) while they're made; the first other click /
key / menu / button waits for them first, then goes on as usual (never dropped or played again).

How: every Python callback Tk calls goes through tkinter.CallWrapper; while notes are being made, the ones Tk calls
straight from its event loop are sorted here (sort):
  VIEW  = scroll / zoom / pan / hover / window drawing: runs at once, the notes on screen as they were (app.stale_ok);
  PASS  = runs at once, but anything reading the notes waits for them (App's note properties, late_attr);
  WAIT  = the notes are finished first (App.finish_notes), then it runs.
Callbacks called from inside another one (a trace a click sets off, a dialog's own clicks) aren't sorted: the notes
they read wait for themselves (late_attr). Tk isn't thread safe: everything here runs on Tk's thread; the notes'
thread only works with its own copies (App._make_coming)."""

import threading
import tkinter as tk
from tkinter import ttk

VIEW, PASS, WAIT = "view", "pass", "wait"
BUTTONS = 0x100 | 0x400 | 0x800 | 0x1000  # (left, right, 4, 5 held; the middle one pans)
MIDDLE = 0x200
MODIFIERS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Caps_Lock", "Win_L", "Win_R",
             "Super_L", "Super_R", "Meta_L", "Meta_R", "Num_Lock", "ISO_Level3_Shift"}
SHOWING = {"Enter", "Leave", "Configure", "Expose", "Map", "Unmap", "Visibility", "Activate", "Deactivate"}
QUIET = {"FocusIn", "FocusOut", "Destroy", "Property", "Colormap"}

_app = None
_depth = 0  # callbacks running (after() timers not counted: a test's events sent from one are sorted as usual)
last_event = None  # the last event handled from Tk's loop (a making started by it doesn't wait for itself)
_main = threading.main_thread()
waits = None  # a list: what made the notes be waited for is added to it (checks in dev/)


def install(app):
    """Called once by App: from now on Tk's callbacks are sorted while app's notes are late (app._bg_notes)."""
    global _app
    _app = app
    if not getattr(tk.CallWrapper, "_notes_wait", False):
        tk.CallWrapper.__call__ = _call
        tk.CallWrapper._notes_wait = True


def _timer(func):
    return getattr(func, "__qualname__", "").endswith("after.<locals>.callit")


def _call(self, *args):
    """tkinter.CallWrapper.__call__ (the same, plus sort)."""
    global _depth, last_event
    try:
        if self.subst:
            args = self.subst(*args)
        func, app = self.func, _app
        if app is None or threading.current_thread() is not _main:
            return func(*args)
        timer = _timer(func)
        how = PASS
        if self.subst and args and not _depth:
            if app._bg_notes:
                how = sort_event(app, args[0])
            last_event = event_key(args[0])
        elif not _depth and not timer and app._bg_notes:
            how = sort_command(app, self)
        if how == WAIT:
            if waits is not None:
                waits.append(("input", repr(args[0]) if self.subst and args else repr(func)))
            app.finish_notes()
        if timer:
            return func(*args)
        _depth += 1
        if how == VIEW:
            app.stale_ok += 1
        try:
            return func(*args)
        finally:
            _depth -= 1
            if how == VIEW:
                app.stale_ok -= 1
    except SystemExit:
        raise
    except:  # noqa: E722 (as tkinter's own)
        self.widget._report_exception()


def view_widget(app, w):
    """w is (inside) the piano roll, one of the scrollbars, the track list or a pane under the roll."""
    from roll.zoombar import ZoomBar
    views = [getattr(app, name, None) for name in ("roll", "track_list", "vel", "tempo_pane")]
    for _ in range(4):
        if w is None or isinstance(w, str):
            return False
        if isinstance(w, (ZoomBar, tk.Scrollbar, ttk.Scrollbar)) or any(w is v for v in views if v is not None):
            return True
        w = getattr(w, "master", None)
    return False


def pass_window(w):
    """w is inside a window marked notes_pass (the note tool windows): its clicks / keys only change settings and
    start new notes (which stops the ones being made), so they run at once; whatever reads the notes still waits
    for them (late_attr)."""
    for _ in range(12):
        if w is None or isinstance(w, str):
            return False
        if getattr(w, "notes_pass", False):
            return True
        w = getattr(w, "master", None)
    return False


def event_key(e):
    """What tells one event from another (every binding of the same event sees the same)."""
    return tuple(getattr(e, k, None) for k in ("serial", "type", "time", "x_root", "y_root", "num", "keysym", "state"))


def sort_event(app, e):
    """VIEW / PASS / WAIT for an event from Tk's loop while notes are being made."""
    how = _sort_event(app, e)
    if how == WAIT and app._bg_serial is not None and event_key(e) == app._bg_serial:
        return PASS  # (the rest of the event that started the making, e.g. the let go's other bindings)
    return how


def _sort_event(app, e):
    kind = str(getattr(e, "type", ""))
    kind = getattr(getattr(e, "type", None), "name", kind)
    state = e.state if isinstance(getattr(e, "state", None), int) else 0
    if kind in SHOWING:
        return VIEW
    if kind in QUIET:
        return PASS
    if pass_window(getattr(e, "widget", None)):
        return VIEW if kind == "Motion" and not state & BUTTONS else PASS
    if kind == "Motion":
        return VIEW if not state & BUTTONS or view_widget(app, e.widget) and not app.roll.drag else WAIT
    if kind == "MouseWheel" or kind in ("ButtonPress", "ButtonRelease") and getattr(e, "num", 0) in (4, 5):
        return VIEW if view_widget(app, e.widget) else PASS
    if kind in ("ButtonPress", "ButtonRelease"):
        if getattr(e, "num", 0) == 2 and e.widget is app.roll:
            return PASS  # (middle-drag pans: its moves are VIEW; a middle-click adding a point reads what it needs)
        from roll.zoombar import ZoomBar
        if isinstance(e.widget, (ZoomBar, tk.Scrollbar, ttk.Scrollbar)):
            return VIEW
        return WAIT
    if kind == "KeyPress":
        return PASS if getattr(e, "keysym", "") in MODIFIERS else WAIT
    if kind == "KeyRelease":
        return PASS
    return WAIT


def sort_command(app, wrapper):
    """A widget's command (button, menu, scrollbar...) or a variable's trace, called from Tk's loop."""
    w, func = wrapper.widget, wrapper.func
    if isinstance(w, (tk.Scrollbar, ttk.Scrollbar)):
        return VIEW
    owner = getattr(func, "__self__", None)
    from roll.zoombar import ZoomBar
    if getattr(func, "__name__", "") == "set" and isinstance(owner, (tk.Scrollbar, ttk.Scrollbar, ZoomBar)):
        return VIEW  # (a list's / canvas's scroll command telling its scrollbar where it is)
    if isinstance(w, tk.Tk):
        return PASS  # (variable traces, the window's close button: reading notes waits)
    if pass_window(w):
        return PASS
    return WAIT


def late_attr(name):
    """An App property for notes made in shapes_changed: read on Tk's thread while they're still being made in the
    background = they're finished first (unless the view is being drawn: app.stale_ok)."""
    raw = "_notes_" + name

    def get(self):
        if self._bg_notes and not self.stale_ok and threading.current_thread() is _main:
            if waits is not None:
                import traceback
                waits.append(("read " + name, "".join(traceback.format_stack(limit=8)[:-1])))
            self.finish_notes()
        try:
            return self.__dict__[raw]
        except KeyError:
            raise AttributeError(name) from None

    def put(self, value):
        self.__dict__[raw] = value

    return property(get, put)


def stale_ok(method):
    """For drawing the view: the notes as they are on screen, never waited for."""
    def run(self, *args, **kw):
        app = getattr(self, "app", None)
        if app is None or not hasattr(app, "stale_ok"):
            return method(self, *args, **kw)
        app.stale_ok += 1
        try:
            return method(self, *args, **kw)
        finally:
            app.stale_ok -= 1
    run.__name__, run.__doc__, run.__qualname__ = method.__name__, method.__doc__, method.__qualname__
    return run
