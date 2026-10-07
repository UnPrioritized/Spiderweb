"""What Spiderweb asks the operating system, each in one small function: the Windows way, the Linux way (untested
until a Linux user reports) or a harmless answer when neither works."""

import ctypes
import sys

WINDOWS = sys.platform == "win32"
ALT = 0x20000 if WINDOWS else 0x8  # Alt held, in a Tk event's state (Linux: Mod1)


def double_click_ms():
    """How quick a second click has to be to make a double click (the system's setting; 500 ms when unknown)."""
    if WINDOWS:
        try:
            return int(ctypes.windll.user32.GetDoubleClickTime())
        except (AttributeError, OSError):
            pass
    return 500


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def free_memory():
    """Bytes of memory free on this PC right now, or None if the system won't say."""
    if WINDOWS:
        st = _MemoryStatus()
        st.dwLength = ctypes.sizeof(st)
        try:
            return st.ullAvailPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
        except (AttributeError, OSError):
            return None
    try:  # Linux: MemAvailable = what can be used without swapping (in kB)
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def dark_system():
    """True when the system's apps are set to dark (Windows: Settings → Colours); False when light or unknown (the
    other systems for now)."""
    if WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
    return False


def error_box(title, msg):
    """An error message that works without a Spiderweb window (e.g. it couldn't start). Nothing if it can't show."""
    if WINDOWS:
        try:
            ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10)
        except (AttributeError, OSError):
            pass
        return
    try:  # elsewhere: a Tk window of its own, hidden behind the message
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, msg, parent=root)
        root.destroy()
    except Exception:
        pass
