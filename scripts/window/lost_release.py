"""A mouse press whose let go never came (user 2026-11-17: after "lots of notes" came up mid-drag, Delete / Ctrl+Z did
nothing: the Gate label still thought it was held).

A window that waits for an answer (a question, a message box, a file picker) can open while a mouse button is held:
in a drag, a number box label, a knob, a pane. The let go then lands on that window, never on what was pressed, so
that stays "held" (shortcuts wait, Ctrl+Z only calls the hold off...). Here every press Tk hands to Python is noted
(notes_wait calls seen() for each event), and when such a window closes, a press from before it that got no let go
gets one: a ButtonRelease sent to the pressed widget, where the mouse was last seen over it, after the callback that
opened the window has finished (when="tail"). One place for every window and every kind of drag."""
import tkinter as tk
from tkinter import commondialog

_held = {}  # button number -> [widget, x, y, state]: pressed, no let go seen yet
_modal = 0  # windows waited for right now (wait_window / a system dialog)


def _kind(e):
    t = getattr(e, "type", "")
    return getattr(t, "name", str(t))


def seen(e):
    """An event a Python callback is about to get (notes_wait._call_sorted)."""
    w = getattr(e, "widget", None)
    if not isinstance(w, tk.Misc):
        return
    kind = _kind(e)
    if kind in ("ButtonPress", "4"):
        if not _modal and getattr(e, "num", 0) in (1, 2, 3):  # (the answering window's own clicks: not noted)
            _held[e.num] = [w, e.x, e.y, e.state if isinstance(e.state, int) else 0]
    elif kind in ("Motion", "6"):
        for rec in _held.values():
            if rec[0] is w:
                rec[1], rec[2] = e.x, e.y
    elif kind in ("ButtonRelease", "5"):
        rec = _held.get(getattr(e, "num", 0))
        if rec is not None and (not _modal or rec[0] is w):  # (a let go on the answering window: not this press's)
            del _held[e.num]


def _let_go(before):
    for n, rec in before.items():
        if _held.get(n) is not rec:
            continue  # (its let go came, or a newer press)
        del _held[n]
        w, x, y, state = rec
        try:
            if w.winfo_exists():
                w.event_generate(f"<ButtonRelease-{n}>", x=x, y=y, state=state | (0x100 << (n - 1)), when="tail")
        except tk.TclError:
            pass


def waiting(fn):
    """fn waits for a window to be answered: presses from before it whose let go went there get it afterwards."""
    def run(*a, **k):
        global _modal
        before = dict(_held)
        _modal += 1
        try:
            return fn(*a, **k)
        finally:
            _modal -= 1
            if before and not _modal:
                _let_go(before)
    run._lost_release = True
    return run


def install():
    """Once: Tk's wait for a window (our questions, simpledialog) and the system's dialogs (message boxes, file
    pickers, colour picker) are watched."""
    if getattr(tk.Misc.wait_window, "_lost_release", False):
        return
    tk.Misc.wait_window = waiting(tk.Misc.wait_window)
    commondialog.Dialog.show = waiting(commondialog.Dialog.show)
