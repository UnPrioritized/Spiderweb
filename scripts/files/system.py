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


def file_version(path):
    """(Windows) A program file's own version as "1.2.3" (the 4th number left out when 0), or None: not Windows,
    no version in it, can't read."""
    if not WINDOWS:
        return None
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        info, n = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(info), ctypes.byref(n)) or n.value < 52:
            return None
        ms, ls = ctypes.cast(info, ctypes.POINTER(wintypes.DWORD * 13)).contents[4:6]  # (VS_FIXEDFILEINFO)
        parts = [ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF]
        if parts[3] == 0:
            parts.pop()
        return ".".join(map(str, parts))
    except (OSError, AttributeError, ValueError):
        return None


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


def downloads_folder():
    """The user's real Downloads folder (moved / renamed ones too), else the home folder."""
    import os
    home = os.path.expanduser("~")
    found = None
    if WINDOWS:
        try:
            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                            ("d", ctypes.c_ubyte * 8)]
            gid = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            out = ctypes.c_wchar_p()
            sh = ctypes.windll.shell32
            if sh.SHGetKnownFolderPath(ctypes.byref(gid), 0, None, ctypes.byref(out)) == 0:  # (FOLDERID_Downloads)
                found = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
        except (AttributeError, OSError):
            pass
    elif not MAC:
        try:
            found = _ask(["xdg-user-dir", "DOWNLOAD"]).strip()
            if os.path.normpath(found) == os.path.normpath(home):  # (none set: it answers the home folder)
                found = None
        except OSError:
            pass
    if not found or not os.path.isdir(found):
        found = os.path.join(home, "Downloads")
    return found if os.path.isdir(found) else home


def show_file(path):
    """Open the file's folder with the file picked (Windows; elsewhere just the folder). False if it couldn't."""
    import os
    import subprocess
    if WINDOWS:
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return True
        except OSError:
            pass
    return open_path(os.path.dirname(path))


_small_icon = []  # (the title bars' icon, loaded once: kept for as long as the program runs)


def small_title_icon(window, path, big_path):
    """The window's title bar icon from path (.ico) at the size Windows draws it there (Tk would shrink the big
    taskbar one: user 2026-10-29, three rings crowded), and its big one (taskbar, Alt+Tab) from big_path at its own
    size: with only a small one set, Windows stretched it for the taskbar (blurry). Nothing elsewhere."""
    if not WINDOWS:
        return
    try:
        u = ctypes.windll.user32
        if not _small_icon:
            u.LoadImageW.restype = ctypes.c_void_p
            u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_uint]
            for p, metric in ((path, 49), (big_path, 11)):  # SM_CXSMICON / SM_CXICON (scaled: we scale ourselves)
                size = u.GetSystemMetrics(metric)
                _small_icon.append(u.LoadImageW(None, p, 1, size, size, 0x10))  # IMAGE_ICON, LR_LOADFROMFILE
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = ctypes.c_void_p(int(window.wm_frame(), 16))
        for kind, icon in enumerate(_small_icon):  # (WM_SETICON: 0 = small, 1 = big)
            if icon:
                u.SendMessageW(hwnd, 0x80, kind, icon)
    except (AttributeError, OSError, ValueError):
        pass


FILE_KIND = "Spiderweb.Project"  # (the name Windows files .spwb under)


def file_link_command(here):
    """-> (command, icon) Windows runs for a double-clicked file: the exe, or pythonw + spiderweb.py from source."""
    import os
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"', f'"{sys.executable}",0'
    exe = sys.executable
    quiet = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(quiet):
        exe = quiet
    return (f'"{exe}" "{os.path.join(here, "spiderweb.py")}" "%1"',
            os.path.join(here, "scripts", "icons", "icon.ico"))


def file_link(ext, here):
    """What a double-clicked file of ext opens now: "here" (this Spiderweb), "other" (another one / program),
    "picked" (the user's own "Always use this app" in Windows names another program: it wins, and only the user can
    change it), "none", or None where it can't be asked (not Windows)."""
    if not WINDOWS:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice") as key:
            chosen = winreg.QueryValueEx(key, "ProgId")[0]
        if chosen and chosen != FILE_KIND:
            return "picked"
    except OSError:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}") as key:
            kind = winreg.QueryValueEx(key, "")[0]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{kind}\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
    except OSError:
        return "none"
    return "here" if command == file_link_command(here)[0] else "other"


def link_files(ext, here, name):
    """Tells Windows that double-clicking an ext file opens this Spiderweb (the user's own part of the registry, no
    installer; its icon = Spiderweb's). True when done."""
    if not WINDOWS:
        return False
    command, icon = file_link_command(here)
    try:
        import winreg
        root = winreg.HKEY_CURRENT_USER
        with winreg.CreateKey(root, rf"Software\Classes\{ext}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, FILE_KIND)
        with winreg.CreateKey(root, rf"Software\Classes\{ext}\OpenWithProgids") as key:
            winreg.SetValueEx(key, FILE_KIND, 0, winreg.REG_NONE, b"")
        with winreg.CreateKey(root, rf"Software\Classes\{FILE_KIND}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, name)
        with winreg.CreateKey(root, rf"Software\Classes\{FILE_KIND}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon)
        with winreg.CreateKey(root, rf"Software\Classes\{FILE_KIND}\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # (SHCNE_ASSOCCHANGED: icons redrawn)
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
