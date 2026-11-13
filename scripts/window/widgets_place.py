"""Window placing helpers (moved out of widgets.py; widgets re-exports every name)."""

import re
import sys

from window import look


def on_screen(widget, x, y):
    """Is the point (x, y) (screen pixels) on one of the screens? (Windows: any monitor; elsewhere the screen Tk
    knows, which on Linux spans them all.)"""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            find = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.POINT, wintypes.DWORD)(
                ("MonitorFromPoint", ctypes.windll.user32))
            return bool(find(wintypes.POINT(x, y), 0))  # (0: no monitor there = none)
        except (OSError, AttributeError):
            pass
    return 0 <= x < widget.winfo_screenwidth() and 0 <= y < widget.winfo_screenheight()


def placed(widget, geo):
    """A window's saved geometry ("WxH+X+Y" / "+X+Y") without its place when its title bar would be off every screen
    (a screen unplugged / made smaller since, user 2026-10-07): it opens where it would the first time. "" = nothing
    left to use."""
    m = re.fullmatch(r"(\d+x\d+)?\+(-?\d+)\+(-?\d+)", geo or "")
    if m and not on_screen(widget, int(m[2]) + 60, int(m[3]) + 15):
        return m[1] or ""
    return geo or ""


def fit_screen(win):
    """Keeps a window whole on its screen, above the taskbar (a big Size / screen scaling on a small screen opened
    windows past its bottom edge): one that can be resized is made smaller, then moved in. Call it after the window
    has its first size / place (remember_place does). A window not shown yet is fitted once its contents are made
    (measuring it now showed it empty first): one that can't be resized just before it shows, one that can right
    after (until then Tk gives its size as 1x1). One that can't be resized is moved in again when it grows (rows
    shown, another page)."""
    if not any(win.resizable()) and not getattr(win, "_fit_size", None):
        win._fit_size = [None]

        def grown(e):
            if e.widget is win and win._fit_size[0] not in (None, (e.width, e.height)):
                win.after_idle(lambda: win.winfo_exists() and _fit_screen(win))
            if e.widget is win:
                win._fit_size[0] = (e.width, e.height)
        win.bind("<Configure>", grown, add="+")
    if win.winfo_ismapped():
        _fit_screen(win)
        return

    def made():
        if not win.winfo_exists():
            return
        if not any(win.resizable()):
            _fit_screen(win)
            return
        once = []

        def shown(e):
            if e.widget is win and not once:
                once.append(True)
                _fit_screen(win)
        win.bind("<Map>", shown, add="+")
    win.after_idle(made)


def _fit_screen(win):
    from files.system import work_area
    if win.state() in ("zoomed", "withdrawn"):
        return
    win.update_idletasks()
    m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", win.wm_geometry())
    if not m:
        return
    w, h, x, y = map(int, m.groups())
    if w <= 1 or h <= 1:  # (no size given: as big as it asks)
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    if win.winfo_ismapped():  # the frame Windows adds: the title bar and the edges (not shown yet: about as big)
        side, title = max(0, win.winfo_rootx() - win.winfo_x()), max(0, win.winfo_rooty() - win.winfo_y())
    else:
        k = getattr(look, "SCREEN_DPI", 96) / 96
        side, title = round(8 * k), round(31 * k)
    left, top, right, bottom = work_area(win, x + w // 2, y + h // 2)
    sizes = any(win.resizable())
    nw = min(w, right - left - 2 * side) if sizes else w
    nh = min(h, bottom - top - title - side) if sizes else h
    nx = max(left, min(x, right - nw - 2 * side))
    ny = max(top, min(y, bottom - nh - title - side))
    if (nw, nh, nx, ny) == (w, h, x, y):
        return
    if sizes:
        least_w, least_h = win.minsize()
        win.minsize(min(least_w, nw), min(least_h, nh))
    win.geometry(f"{nw}x{nh}+{nx}+{ny}" if sizes else f"+{nx}+{ny}")


def set_least(win, w, h):
    """win.minsize(w, h), never more than its screen has room for above the taskbar (Windows' frame counted): a
    window that needs more room for its texts than a small screen has stays on the screen (hunt 2026-10-19)."""
    from files.system import work_area
    if win.winfo_ismapped():
        side, title = max(0, win.winfo_rootx() - win.winfo_x()), max(0, win.winfo_rooty() - win.winfo_y())
    else:
        k = getattr(look, "SCREEN_DPI", 96) / 96
        side, title = round(8 * k), round(31 * k)
    left, top, right, bottom = work_area(win, win.winfo_rootx() + win.winfo_width() // 2,
                                         win.winfo_rooty() + win.winfo_height() // 2)
    least = (max(1, min(int(w), right - left - 2 * side)), max(1, min(int(h), bottom - top - title - side)))
    if tuple(win.minsize()) != least:
        win.minsize(*least)


PLACE = r"(\d+x\d+)?\+-?\d+\+-?\d+"  # a remembered window place: "WxH+X+Y" (one that can be resized) or "+X+Y"


def remember_place(win, name):
    """User 2026-10-09: every window opens where it was last, and as big when it can be resized. Call it after the
    window has set its own first size / place (that's used when nothing is remembered, or the place is off every
    screen). The places are the main window's window_places (saved in the autosave)."""
    places = win._root().__dict__.setdefault("window_places", {})
    sizes = any(win.resizable())
    geo = placed(win, places.get(name, ""))
    if geo and not sizes:
        geo = geo[geo.index("+"):] if "+" in geo else ""
    if geo:
        win.geometry(geo)
    fit_screen(win)

    def keep(e=None):
        if (e is None or e.widget is win) and win.winfo_ismapped():
            geo = win.wm_geometry()
            places[name] = geo if sizes else geo[geo.index("+"):]
    win.bind("<Configure>", keep, add="+")  # (moved or resized: remembered at once)


def centre_over(win, parent):
    """Places win across the middle of parent (a third of the way down), never past the screen's top left; call it
    before remember_place (a remembered place wins)."""
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
