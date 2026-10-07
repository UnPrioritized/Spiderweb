"""The Windows clipboard through ctypes: raw bytes in any format (domino_clip.py's notes) and plain text
(share.py's shared shapes). Tk's own clipboard is left out there: it only handles text, and only while Tk runs.
Elsewhere (Linux, untested) only text works, through Tk's clipboard (what Spiderweb copied may go when it closes,
unless the desktop keeps clipboards); RAW is False there (no Domino buttons)."""

import ctypes
from ctypes import wintypes

from files.lang import tr
from files.system import WINDOWS

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


def _open(user32, kernel32):
    for _ in range(10):  # another program may have it open for a moment
        if user32.OpenClipboard(None):
            return True
        kernel32.Sleep(20)
    return False


def put(fmt, raw):
    """raw bytes -> the clipboard as format fmt (replaces what's there). Returns False if the clipboard was busy."""
    user32, kernel32 = _api()
    h = kernel32.GlobalAlloc(0x0002, len(raw))  # GMEM_MOVEABLE
    if not h:
        raise MemoryError(tr("domino_clip.not_enough_memory_for_the_clipboard"))
    ctypes.memmove(kernel32.GlobalLock(h), raw, len(raw))
    kernel32.GlobalUnlock(h)
    if not _open(user32, kernel32):
        kernel32.GlobalFree(h)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(fmt, h):  # on success the clipboard owns h
            kernel32.GlobalFree(h)
            return False
    finally:
        user32.CloseClipboard()
    return True


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
    if not WINDOWS:
        root = _tk()
        if root is None:
            return False
        root.clipboard_clear()
        root.clipboard_append(text)
        return True
    return put(TEXT, (text + "\0").encode("utf-16-le"))


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
