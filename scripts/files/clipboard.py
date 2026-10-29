"""The Windows clipboard through ctypes: raw bytes in any format (domino_clip.py's notes) and plain text
(share.py's shared shapes). Tk's own clipboard is left out there: it only handles text, and only while Tk runs.
Elsewhere (Linux, untested) only text works, through Tk's clipboard (what Spiderweb copied may go when it closes,
unless the desktop keeps clipboards); RAW is False there (no Domino buttons)."""

import ctypes

from files.lang import tr
from files.system import WINDOWS, wintypes

TEXT = 13  # CF_UNICODETEXT
RAW = WINDOWS  # raw bytes in any format (put / get / registered) work


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
    if not WINDOWS:
        return hash(get_text())
    return _api()[0].GetClipboardSequenceNumber()


def copy_count():
    """count(), looked up when called (tests put a pretend one in its place, like put / get)."""
    return count()


def registered(name):
    """The number of a clipboard format known by its name (made the first time it's asked for)."""
    return _api()[0].RegisterClipboardFormatW(name)


def has(fmt):
    """True if the clipboard holds format fmt (asked without opening it or reading the bytes)."""
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
