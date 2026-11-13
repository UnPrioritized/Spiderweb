"""Mouse wheel rules: click first, Linux / Mac wheel + buttons (moved out of widgets.py; widgets re-exports every name)."""

import re
import sys
import tkinter as tk
from tkinter import ttk


_glide = None  # App.wheel_gliding (set_glide)


def set_glide(fn):
    """fn(event) -> True when a scroll going on took the wheel (App.wheel_gliding: the side panel, the synth's tabs)."""
    global _glide
    _glide = fn


_last = {}  # each window's widget last clicked or given the keyboard (took)


def took(w):
    """w was clicked / given the keyboard (click_first's bindings): the wheel may change it now (wheel_ready). A
    widget with .mark_changed() (knobs: their ring) is told when it gets or loses the mark."""
    try:
        top, path = str(w.winfo_toplevel()), str(w)
    except (AttributeError, tk.TclError):  # (a dropdown's open list: Tk's own, no widget here)
        return
    was = _last.get(top)
    _last[top] = path
    if was == path:
        return
    for p in (was, path):
        try:
            got = w.nametowidget(p) if p else None
        except KeyError:
            got = None
        if got is not None and hasattr(got, "mark_changed"):
            got.mark_changed()


def wheel_widget(w):
    """Does the wheel change w once it's clicked (dropdown, spin box, number box, knob, Claw menu)?"""
    return (isinstance(w, (ttk.Combobox, ttk.Spinbox, tk.Spinbox)) or getattr(w, "_scrub", False)
            or getattr(w, "_wheel", False))


def can_change(w):
    """Not greyed: the wheel may change it (greyed: the wheel goes on to the window)."""
    try:
        return not (w.instate(["disabled"]) if hasattr(w, "instate") else str(w.cget("state")) == "disabled")
    except tk.TclError:
        return True


def wheel_ready(w):
    """CLICK FIRST (user 2026-10-21: knobs and choices changed while scrolling past them; like Windows' own boxes):
    the mouse wheel changes a dropdown, number box, knob... only once it's the last thing clicked (or given the
    keyboard) in its window; otherwise the wheel goes on to the window, which scrolls. Kept by took, not asked of
    Tk's focus: that isn't set while another program has the keyboard."""
    try:
        return _last.get(str(w.winfo_toplevel())) == str(w)
    except tk.TclError:
        return False


def wheel_turns(w, e):
    """Should the wheel (event e) over w change its value? False: it's for the window (not clicked, or a scroll going
    on took it). Value widgets return "break" when it's True and they changed."""
    return wheel_ready(w) and not (_glide and _glide(e))


def _does_something(script):
    return any(line.strip() and not line.strip().startswith("#") for line in script.splitlines())


def click_first(root, classes=("TCombobox", "TSpinbox", "Spinbox", "TScale", "Scale")):
    """Tk's own wheel steps on these classes (every Shift / Alt one too) only by the click-first rule (wheel_turns);
    every click remembered for it (took; a keyboard move only marks a wheel_widget, never takes the mark away: picks
    hand the keyboard back to the piano roll / synth / drawer). Tabs never change on the wheel (user 2026-10-21, like
    Windows' own). Linux with Tk 8.6: linux_wheel first."""
    old_x11 = linux_wheel(root)
    root.bind_all("<ButtonPress>", lambda e: e.num in (4, 5, 6, 7) or took(e.widget), add="+")
    root.bind_all("<FocusIn>", lambda e: wheel_widget(e.widget) and took(e.widget), add="+")
    for seq in root.bind_class("TNotebook"):
        if "MouseWheel" in seq or "Button-4" in seq or "Button-5" in seq:
            root.unbind_class("TNotebook", seq)
    for cls in classes:
        steps = {seq: root.bind_class(cls, seq) for seq in root.bind_class(cls) if "MouseWheel" in seq}
        if old_x11:  # (Tk 8.6 on Linux: its steps are on buttons 4 / 5; the wheel comes as <MouseWheel> now)
            up, down = root.bind_class(cls, "<Button-4>"), root.bind_class(cls, "<Button-5>")
            for seq in root.bind_class(cls):
                if "Button-4" in seq or "Button-5" in seq:
                    root.unbind_class(cls, seq)
            if (up or down) and "<MouseWheel>" not in steps:
                steps["<MouseWheel>"] = (up, down)
        for seq, own in steps.items():
            if isinstance(own, str) and not _does_something(own):
                continue  # (Tk's "ignore the event": on to the window)

            def wheel(e, own=own):
                if not wheel_ready(e.widget) or not can_change(e.widget):
                    return None  # (on to the window: it scrolls)
                if not (_glide and _glide(e)):
                    script = own if isinstance(own, str) else own[0] if e.delta > 0 else own[1]
                    for k, v in (("%W", e.widget), ("%D", e.delta), ("%s", e.state), ("%x", e.x), ("%y", e.y)):
                        script = script.replace(k, str(v))
                    root.tk.eval(script)
                return "break"
            root.bind_class(cls, seq, wheel)


def linux_wheel(root):
    """Linux with Tk 8.6 sends the wheel as presses of buttons 4 / 5 (Windows and Tk 8.7+: <MouseWheel>): each made
    a <MouseWheel> of Windows' size on the widget under the mouse, so the program's wheel works the same everywhere
    (user 2026-10-21). Classes that scroll on buttons 4 / 5 themselves (lists, text) keep theirs. True when it's on."""
    if root.tk.call("tk", "windowingsystem") != "x11":
        return False
    level = [int(n) for n in str(root.tk.call("info", "patchlevel")).split(".")[:2]]
    if level >= [8, 7]:
        return False

    def wheel(e, d):
        w = e.widget
        if not isinstance(w, tk.Misc) or "<Button-4>" in root.bind_class(w.winfo_class()):
            return None
        w.event_generate("<MouseWheel>", delta=120 * d, x=e.x, y=e.y, state=e.state)
        return "break"
    root.bind_all("<Button-4>", lambda e: wheel(e, 1), add="+")
    root.bind_all("<Button-5>", lambda e: wheel(e, -1), add="+")
    return True


_BUTTON_23 = re.compile(r"((?:Button|ButtonPress|ButtonRelease)-|<|B)([23])(?=[->])")


def mac_buttons():
    """(Mac, untested) Tk 8.6 on macOS calls the right button 2 and the middle one 3 (the other way round from
    Windows, Linux and Tk 8.7+): every binding made after this has them swapped, so right-click menus and middle-drag
    panning work as written. Nothing elsewhere."""
    if sys.platform != "darwin" or tk.TkVersion >= 8.7 or getattr(tk.Misc, "_mac_swapped", False):
        return
    plain = tk.Misc._bind

    def _bind(self, what, sequence, func, add, needcleanup=1):
        if isinstance(sequence, str):
            sequence = _BUTTON_23.sub(lambda m: m[1] + ("3" if m[2] == "2" else "2"), sequence)
        return plain(self, what, sequence, func, add, needcleanup)
    tk.Misc._bind = _bind
    tk.Misc._mac_swapped = True


def wheel_unit():
    """How much a Tk wheel event's delta is per wheel step: 120, but Tk 8.6 on macOS counts 1 (untested there)."""
    return 1 if sys.platform == "darwin" and tk.TkVersion < 8.7 else 120
