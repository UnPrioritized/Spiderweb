"""What Spiderweb asks the operating system, each in one small function: the Windows way, the Linux way (a Linux
user reports it works), the macOS way (untested) or a harmless answer when none works."""

import ctypes
import sys

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"
ALT = 0x20000 if WINDOWS else 0x10 if MAC else 0x8  # Alt held, in a Tk event's state (Linux: Mod1; Mac: Option)

try:
    from ctypes import wintypes  # Windows' C types (import it from here, never from ctypes)
except (ImportError, ValueError):  # (Linux on an older Python can't load it: files describing Windows calls still
    class _PlainTypes:             # have to load, so every name is a plain int there; Windows calls never run)
        def __getattr__(self, name):
            return ctypes.c_int
    wintypes = _PlainTypes()


def double_click_ms():
    """How quick a second click has to be to make a double click (the system's setting; 500 ms when unknown)."""
    if WINDOWS:
        try:
            return int(ctypes.windll.user32.GetDoubleClickTime())
        except (AttributeError, OSError):
            pass
    return 500


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def work_area(widget, x, y):
    """(left, top, right, bottom) in screen pixels of the room windows have on the screen nearest the point (x, y):
    Windows = that monitor less its taskbar; elsewhere the whole screen Tk knows."""
    if WINDOWS:
        try:
            user32 = ctypes.windll.user32
            find = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.POINT, wintypes.DWORD)(("MonitorFromPoint", user32))
            monitor = find(wintypes.POINT(int(x), int(y)), 2)  # (2: the nearest monitor)
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            ask = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)(("GetMonitorInfoW", user32))
            if monitor and ask(monitor, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
        except (OSError, AttributeError):
            pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


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
    if MAC:  # (untested) vm_stat's free + inactive + speculative pages, the page size on its first line
        try:
            text = _ask(["vm_stat"])
            page = int(text.split("page size of")[1].split()[0])
            pages = {k.strip(): v.strip().rstrip(".") for k, v in
                     (line.split(":", 1) for line in text.splitlines()[1:] if ":" in line)}
            return page * sum(int(pages.get(k, 0)) for k in ("Pages free", "Pages inactive", "Pages speculative"))
        except (OSError, ValueError, IndexError):
            return None
    try:  # Linux: MemAvailable = what can be used without swapping (in kB)
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def total_memory():
    """Bytes of memory this PC has, or None if the system won't say."""
    if WINDOWS:
        st = _MemoryStatus()
        st.dwLength = ctypes.sizeof(st)
        try:
            return st.ullTotalPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
        except (AttributeError, OSError):
            return None
    if MAC:  # (untested)
        try:
            return int(_ask(["sysctl", "-n", "hw.memsize"]))
        except (OSError, ValueError):
            return None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def cpu_name():
    """The processor's name (e.g. "AMD Ryzen 7 5800X 8-Core Processor"), or "" if the system won't say."""
    if WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            return ""
    if MAC:  # (untested)
        try:
            return _ask(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        except OSError:
            return ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, IndexError):
        pass
    return ""


def _ask(command):
    """(Mac) What a small system program prints. OSError when it can't run or fails."""
    import subprocess
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5, check=True).stdout
    except (subprocess.SubprocessError, UnicodeDecodeError) as e:
        raise OSError(e) from None


def dark_system():
    """True when the system's apps are set to dark (Windows: Settings → Colours; Mac: Appearance); False when light
    or unknown (Linux for now)."""
    if WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
    elif MAC:  # (untested: "Dark" only while dark; light = the setting is missing and the program fails)
        try:
            return _ask(["defaults", "read", "-g", "AppleInterfaceStyle"]).strip() == "Dark"
        except OSError:
            pass
    return False


def open_path(path, text=False):
    """Open a folder, or a file in its usual program (text: a plain text file without a known ending, Notepad on
    Windows). False if it couldn't be opened."""
    import os
    import subprocess
    try:
        if WINDOWS:
            if text:
                subprocess.Popen(["notepad.exe", path])
            else:
                os.startfile(path)
        elif MAC:  # (untested; -t = TextEdit)
            subprocess.Popen(["open", "-t", path] if text else ["open", path])
        else:  # (Linux: the desktop's own opener)
            subprocess.Popen(["xdg-open", path])
        return True
    except (OSError, AttributeError):
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
