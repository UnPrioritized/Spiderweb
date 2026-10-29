"""The Windows clipboard through ctypes: raw bytes in any format (domino_clip.py's notes) and plain text
(share.py's shared shapes). Tk's own clipboard is left out there: it only handles text, and only while Tk runs.
Linux (X11, Wayland through XWayland; "quiet" support, user 2026-10-29: WSL-checked only): Tk's clipboard puts
raw bytes under the same format names as Windows (a format = its name; Wine hands registered formats over by name,
so Domino under Wine may see them); another program's copy is read straight from X (_x_read: Tk refuses big ones
sent in one piece). What Spiderweb copied goes when it closes, unless the desktop keeps clipboards. Mac: text only
(RAW False: Tk there can't hold other formats)."""

import ctypes
import re

from files.lang import tr
from files.system import MAC, WINDOWS, wintypes

TEXT = 13  # CF_UNICODETEXT
X11 = not (WINDOWS or MAC)  # (Linux / BSD: Tk on X; untested on a real desktop)
RAW = WINDOWS or X11  # raw bytes in any format (put / get / registered) work
_own = [0]  # (X11: Spiderweb's copies so far: the copy count while Spiderweb still holds the clipboard)


def _api():
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    return user32, kernel32


def count():
    """A number Windows raises every time anything is put on the clipboard, by any program. Elsewhere: one made
    from the text held (so the same text copied again doesn't count)."""
    if X11:  # (Spiderweb holds it: its own count; else what's held, by its formats + text)
        return ("own", _own[0]) if _x_owned() else hash((tuple(_x_targets()), get_text()))
    if not WINDOWS:
        return hash(get_text())
    return _api()[0].GetClipboardSequenceNumber()


def copy_count():
    """count(), looked up when called (tests put a pretend one in its place, like put / get)."""
    return count()


def registered(name):
    """The number of a clipboard format known by its name (made the first time it's asked for). X11: the name."""
    if X11:
        return name
    return _api()[0].RegisterClipboardFormatW(name)


def has(fmt):
    """True if the clipboard holds format fmt (asked without opening it or reading the bytes)."""
    if X11:
        return fmt in _x_targets()
    user32 = _api()[0]
    return bool(user32.IsClipboardFormatAvailable(fmt))


def _open(user32, kernel32):
    for _ in range(10):  # another program may have it open for a moment
        if user32.OpenClipboard(None):
            return True
        kernel32.Sleep(20)
    return False


def put(fmt, raw):
    """raw bytes -> the clipboard as format fmt (replaces what's there). Returns False if the clipboard was busy."""
    return fmt in put_many([(fmt, raw)])


def put_many(items):
    """[(format, raw bytes)] -> the clipboard, all at once (replaces what's there; a program pasting picks the format
    it knows). Returns the formats that went on ([] if the clipboard was busy). MemoryError when one doesn't fit
    (nothing put)."""
    if X11:
        return _x_put(items)[1]
    user32, kernel32 = _api()
    held = []
    for fmt, raw in items:
        h = kernel32.GlobalAlloc(0x0002, len(raw))  # GMEM_MOVEABLE
        if not h:
            for _, h in held:
                kernel32.GlobalFree(h)
            raise MemoryError(tr("domino_clip.not_enough_memory_for_the_clipboard"))
        ctypes.memmove(kernel32.GlobalLock(h), raw, len(raw))
        kernel32.GlobalUnlock(h)
        held.append((fmt, h))
    if not _open(user32, kernel32):
        for _, h in held:
            kernel32.GlobalFree(h)
        return []
    put_on = []
    try:
        user32.EmptyClipboard()
        for fmt, h in held:
            if user32.SetClipboardData(fmt, h):  # on success the clipboard owns h
                put_on.append(fmt)
            else:
                kernel32.GlobalFree(h)
    finally:
        user32.CloseClipboard()
    return put_on


def get(fmt):
    """The clipboard's bytes in format fmt, b"" if it has none, or None if the clipboard was busy."""
    if X11:
        return _x_get(fmt)
    user32, kernel32 = _api()
    if not _open(user32, kernel32):
        return None
    try:
        h = user32.GetClipboardData(fmt)
        if not h:
            return b""
        at = kernel32.GlobalLock(h)
        if not at:
            return b""
        try:
            return ctypes.string_at(at, kernel32.GlobalSize(h))
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def _tk():
    """The Tk window whose clipboard is used when not on Windows (None before there is one)."""
    import tkinter
    return tkinter._default_root


def put_text(text):
    """Text -> the clipboard. False if the clipboard was busy."""
    return put_text_and(text)[0]


def put_text_and(text, also=()):
    """Text -> the clipboard, also: [(format, raw bytes)] put beside it (Windows only; extras, so no room for them =
    the text alone). Returns (text went on, the extras went on); the text didn't = the clipboard was busy."""
    if X11:
        try:
            went, put_on = _x_put(also, text)
        except MemoryError:
            if not also:
                raise
            went, put_on = _x_put((), text)
        return went, went and len(put_on) == len(also)
    if not WINDOWS:
        root = _tk()
        if root is None:
            return False, False
        root.clipboard_clear()
        root.clipboard_append(text)
        return True, not also
    items = [(TEXT, (text + "\0").encode("utf-16-le"))] + list(also)
    try:
        put_on = put_many(items)
    except MemoryError:
        if not also:
            raise
        put_on = put_many(items[:1])
    return TEXT in put_on, len(put_on) == len(items)


def get_text():
    """The clipboard's text ("" if it holds none), or None if the clipboard was busy."""
    if not WINDOWS:
        import tkinter
        root = _tk()
        if root is None:
            return None
        try:
            return root.clipboard_get()
        except tkinter.TclError:  # (empty, or no text in it)
            return ""
    raw = get(TEXT)
    if raw is None:
        return None
    text = raw.decode("utf-16-le", errors="ignore")
    end = text.find("\0")
    return text if end < 0 else text[:end]


# ---------------------------------------------------------------- X11 (Linux; untested on a real desktop)

_x_owner = [None]  # (the X window holding the clipboard after Spiderweb's last copy: Tk's hidden clipboard window)
_x_conn = []  # (one X connection kept for asking who holds the clipboard: [display, CLIPBOARD atom] or [None])


def _x_clip_owner():
    """The X window holding the clipboard now (0 = nobody), None when X can't be asked."""
    if not _x_conn:
        x = _x_lib()
        dpy = x.XOpenDisplay(None) if x else None
        _x_conn.extend([dpy, x.XInternAtom(dpy, b"CLIPBOARD", 0)] if dpy else [None, 0])
    dpy, clip = _x_conn
    return _x_lib().XGetSelectionOwner(dpy, clip) if dpy else None


def _x_owned():
    """True while Spiderweb's own Tk holds the clipboard (what it copied last is still there). (Tk's `selection own`
    can't tell: its clipboard is held by a hidden window it doesn't name; measured in WSL.)"""
    if _x_owner[0] is None:
        return False
    return _x_clip_owner() == _x_owner[0]


def _x_targets():
    """The formats the clipboard offers now, by name ([] if none / can't tell)."""
    root = _tk()
    if root is None:
        return []
    try:
        got = root.selection_get(selection="CLIPBOARD", type="TARGETS")
    except Exception:
        return []
    try:  # (a Tcl list: "Spiderweb Notes" comes braced, one name)
        return [str(t) for t in root.tk.splitlist(got)]
    except Exception:
        return got.split() if isinstance(got, str) else [str(t) for t in got]


def _x_put(items, text=None):
    """Text (None: none) and [(name, raw bytes)] -> the clipboard through Tk, replacing what's there: (put, the
    names put). Each format's bytes ride as a Latin-1 string with format STRING, which Tk hands out byte for byte
    (zeros too; big ones in pieces). MemoryError when they don't fit."""
    root = _tk()
    if root is None:
        return False, []
    import tkinter
    try:
        root.clipboard_clear()
        if text is not None:
            root.clipboard_append(text)
        names = []
        for name, raw in items:
            root.clipboard_append(bytes(raw).decode("latin-1"), type=name, format="STRING")
            names.append(name)
    except tkinter.TclError:
        return False, []
    _own[0] += 1
    root.update_idletasks()  # (Tk takes the clipboard over at once; asked who holds it now)
    _x_owner[0] = _x_clip_owner() or None
    return True, names


_HEX = re.compile(r"\s*0x[0-9a-fA-F]{1,2}(\s+0x[0-9a-fA-F]{1,2})*\s*")


def _x_get(name):
    """The clipboard's bytes in format `name`, b"" if it has none, None if it can't be read."""
    root = _tk()
    if root is None:
        return None
    import tkinter
    if not _x_owned():  # (another program's: read straight from X, any size)
        got = _x_read(name)
        if got is not None:
            return got
    try:  # (Spiderweb's own copy, or X itself couldn't be asked: through Tk)
        s = root.selection_get(selection="CLIPBOARD", type=name)
    except tkinter.TclError:
        return b""
    if _HEX.fullmatch(s[:4096]) and _HEX.fullmatch(s):  # (another program's bytes as Tk lists them: "0x1 0x2 ...")
        return bytes(int(t, 16) for t in s.split())
    try:
        return s.encode("latin-1")
    except UnicodeEncodeError:
        return b""


_xlib = []


def _x_lib():
    """libX11 through ctypes (None when it can't be loaded)."""
    if not _xlib:
        try:
            x = ctypes.CDLL("libX11.so.6")
            vp, ul = ctypes.c_void_p, ctypes.c_ulong
            x.XOpenDisplay.argtypes, x.XOpenDisplay.restype = [ctypes.c_char_p], vp
            x.XDefaultRootWindow.argtypes, x.XDefaultRootWindow.restype = [vp], ul
            x.XCreateSimpleWindow.argtypes = [vp, ul, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                              ctypes.c_uint, ul, ul]
            x.XCreateSimpleWindow.restype = ul
            x.XSelectInput.argtypes = [vp, ul, ctypes.c_long]
            x.XInternAtom.argtypes, x.XInternAtom.restype = [vp, ctypes.c_char_p, ctypes.c_int], ul
            x.XConvertSelection.argtypes = [vp, ul, ul, ul, ul, ul]
            x.XFlush.argtypes = x.XPending.argtypes = x.XCloseDisplay.argtypes = [vp]
            x.XNextEvent.argtypes = [vp, vp]
            x.XGetWindowProperty.argtypes = [vp, ul, ul, ctypes.c_long, ctypes.c_long, ctypes.c_int, ul,
                                             ctypes.POINTER(ul), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ul),
                                             ctypes.POINTER(ul), ctypes.POINTER(vp)]
            x.XFree.argtypes = [vp]
            x.XDeleteProperty.argtypes = [vp, ul, ul]
            x.XDestroyWindow.argtypes = [vp, ul]
            x.XGetSelectionOwner.argtypes, x.XGetSelectionOwner.restype = [vp, ul], ul
            x.XConnectionNumber.argtypes, x.XConnectionNumber.restype = [vp], ctypes.c_int
            _xlib.append(x)
        except (OSError, AttributeError):
            _xlib.append(None)
    return _xlib[0]


X_WAIT_S = 5.0  # (each piece of a big copy)
X_ASK_S, X_ASKS = 1.0, 4  # (the other program's first answer: asked again after this long, this many times)


def _x_read(name):
    """Another program's copy in format `name` straight from X (Tk refuses ones over ~400 KB sent in one piece, and
    lists bytes as "0x.." text): a window of our own asks for it (XConvertSelection), then the property is read
    whole, or piece by piece for an INCR transfer. bytes; b"" = not offered; None = X couldn't be asked."""
    import select
    import time
    x = _x_lib()
    dpy = x.XOpenDisplay(None) if x else None
    if not dpy:
        return None
    win = 0
    try:
        win = x.XCreateSimpleWindow(dpy, x.XDefaultRootWindow(dpy), 0, 0, 1, 1, 0, 0, 0)
        x.XSelectInput(dpy, win, 1 << 22)  # PropertyChangeMask
        atom = lambda s: x.XInternAtom(dpy, s.encode(), 0)
        clip, prop, incr = atom("CLIPBOARD"), atom("SPIDERWEB_CLIP"), atom("INCR")
        ev = (ctypes.c_long * 24)()  # (XEvent; every field used here sits on a long's place, 32 and 64 bit)
        fd = x.XConnectionNumber(dpy)

        def wait(kind, fits, wait_s=X_WAIT_S):  # (woken the moment X sends something: a big copy = thousands of pieces)
            end = time.monotonic() + wait_s
            while True:
                while x.XPending(dpy):
                    x.XNextEvent(dpy, ev)
                    # (the type is an int on a long's place: its other half is whatever was there, measured)
                    if (ev[0] & 0xFFFFFFFF) == kind and fits():
                        return True
                left = end - time.monotonic()
                if left <= 0:
                    return False
                select.select([fd], [], [], left)

        for _ in range(X_ASKS):  # (asked again when no answer comes: an owner may miss one)
            x.XConvertSelection(dpy, clip, atom(name), prop, win, 0)
            x.XFlush(dpy)
            if wait(31, lambda: True, X_ASK_S):  # SelectionNotify
                break
        else:
            return None
        if ev[7] == 0:  # (property None: the format isn't offered)
            return b""

        def take():  # -> (type, bytes) of the property, deleted; None = not 8-bit data
            t, f, n, after, data = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(), \
                ctypes.c_void_p()
            if x.XGetWindowProperty(dpy, win, prop, 0, 0x1FFFFFFF, 1, 0, ctypes.byref(t), ctypes.byref(f),
                                    ctypes.byref(n), ctypes.byref(after), ctypes.byref(data)) != 0:
                return None
            try:
                if t.value == incr:
                    return t.value, b""
                if f.value != 8:
                    return None
                return t.value, ctypes.string_at(data, n.value) if data.value else b""
            finally:
                if data.value:
                    x.XFree(data)

        got = take()
        if got is None:
            return None
        if got[0] != incr:
            return got[1]
        parts = []  # (INCR: each new piece put on the property, an empty one ends it)
        while True:
            if not wait(28, lambda: ev[5] == prop and (ev[7] & 0xFFFFFFFF) == 0):  # PropertyNotify, NewValue
                return None
            got = take()
            if got is None:
                return None
            if not got[1]:
                return b"".join(parts)
            parts.append(got[1])
    except (OSError, ValueError, MemoryError):
        return None
    finally:
        if win:
            x.XDestroyWindow(dpy, win)
        x.XCloseDisplay(dpy)
