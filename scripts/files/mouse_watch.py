"""Seeing the left mouse button pressed / let go ANYWHERE on the screen, with Ctrl / Shift as they were then
(Windows only): Draw on Domino watches the boxes the user drags in Domino's piano roll (PLAN.md stage B, 2026-11-12).

Raw input sent to a message-only window in a thread of its own (RIDEV_INPUTSINK: also while another program is in
front). Picked over a low-level mouse hook: a hook makes Windows WAIT for Python on every mouse move (a busy Spiderweb
would make the whole PC's mouse stutter); raw input is only posted, so a busy Spiderweb just sees it later. Each
message keeps the cursor's place AT THE TIME it was made (MSG.pt) and its time (MSG.time, GetTickCount's
milliseconds). Ctrl / Shift come from the raw keyboard in the same stream, so they're right even when read late.
Other systems: nothing is watched (available() False)."""

import collections
import ctypes
import threading

from files import system

WM_INPUT, WM_QUIT = 0x00FF, 0x0012
RID_INPUT, RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0x10000003, 0, 1
RIDEV_INPUTSINK, RIDEV_REMOVE = 0x100, 0x1
LEFT_DOWN, LEFT_UP = 0x0001, 0x0002  # RAWMOUSE usButtonFlags
KEY_BREAK = 0x1                      # RAWKEYBOARD Flags: key let go
CTRL_KEYS, SHIFT_KEYS = (0x11, 0xA2, 0xA3), (0x10, 0xA0, 0xA1)
MOST = 512  # events kept unread (the oldest dropped)


def available():
    return system.WINDOWS


if system.WINDOWS:
    from files.system import wintypes as W

    class _DEVICE(ctypes.Structure):
        _fields_ = [("page", ctypes.c_ushort), ("usage", ctypes.c_ushort), ("flags", W.DWORD), ("target", W.HWND)]

    class _HEADER(ctypes.Structure):
        _fields_ = [("type", W.DWORD), ("size", W.DWORD), ("device", W.HANDLE), ("wparam", W.WPARAM)]

    class _MOUSE(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_ushort), ("_pad", ctypes.c_ushort),  # (the buttons' union is 4-aligned)
                    ("button_flags", ctypes.c_ushort), ("button_data", ctypes.c_ushort),
                    ("raw_buttons", ctypes.c_ulong), ("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("extra", ctypes.c_ulong)]

    class _KEYBOARD(ctypes.Structure):
        _fields_ = [("make", ctypes.c_ushort), ("flags", ctypes.c_ushort), ("reserved", ctypes.c_ushort),
                    ("vkey", ctypes.c_ushort), ("message", ctypes.c_uint), ("extra", ctypes.c_ulong)]

    class _DATA(ctypes.Union):
        _fields_ = [("mouse", _MOUSE), ("keyboard", _KEYBOARD)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("header", _HEADER), ("data", _DATA)]

    class _MSG(ctypes.Structure):
        _fields_ = [("hwnd", W.HWND), ("message", ctypes.c_uint), ("wparam", W.WPARAM), ("lparam", W.LPARAM),
                    ("time", W.DWORD), ("pt", W.POINT)]

    class _WNDCLASS(ctypes.Structure):
        _fields_ = [("style", ctypes.c_uint), ("proc", ctypes.c_void_p), ("cls_extra", ctypes.c_int),
                    ("wnd_extra", ctypes.c_int), ("instance", W.HINSTANCE), ("icon", W.HANDLE),
                    ("cursor", W.HANDLE), ("brush", W.HANDLE), ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]


def _api():
    """Own copies of user32 / kernel32 (their argument types set here never touch other code's)."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.CreateWindowExW.restype = W.HWND
    u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, W.HWND, W.HANDLE, W.HINSTANCE, ctypes.c_void_p]
    u.DefWindowProcW.restype = W.LPARAM
    u.DefWindowProcW.argtypes = [W.HWND, ctypes.c_uint, W.WPARAM, W.LPARAM]
    u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
    u.RegisterRawInputDevices.argtypes = [ctypes.POINTER(_DEVICE), ctypes.c_uint, ctypes.c_uint]
    u.GetRawInputData.argtypes = [W.HANDLE, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint),
                                  ctypes.c_uint]
    u.GetMessageW.argtypes = [ctypes.POINTER(_MSG), W.HWND, ctypes.c_uint, ctypes.c_uint]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
    u.DispatchMessageW.restype = W.LPARAM
    u.PostThreadMessageW.argtypes = [W.DWORD, ctypes.c_uint, W.WPARAM, W.LPARAM]
    u.DestroyWindow.argtypes = [W.HWND]
    u.UnregisterClassW.argtypes = [W.LPCWSTR, W.HINSTANCE]
    k.GetModuleHandleW.restype = W.HINSTANCE
    k.GetModuleHandleW.argtypes = [W.LPCWSTR]
    return u, k


class Watch:
    """events: a deque of (pressed, x, y, ms, ctrl, shift): pressed True = left button down, False = let go; x / y
    = the cursor then (screen pixels, as GetCursorPos gives them here); ms = GetTickCount then. Read with take()."""

    def __init__(self):
        self.events = collections.deque(maxlen=MOST)
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()
        self.on = False
        self._ctrl = set()
        self._shift = set()

    def start(self):
        """True once watching (False: not on this system, or Windows refused)."""
        if not available():
            return False
        if self._thread is not None:
            return self.on
        self._thread = threading.Thread(target=self._run, name="mouse watch", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self.on

    def stop(self):
        t, self._thread = self._thread, None
        if t is not None and self._tid:
            _api()[0].PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            t.join(1.0)
        self.on = False

    def take(self):
        """The events since the last take, oldest first."""
        out = []
        while self.events:
            try:
                out.append(self.events.popleft())
            except IndexError:
                break
        return out

    def _run(self):
        u, k = _api()
        self._tid = k.GetCurrentThreadId()
        inst = k.GetModuleHandleW(None)
        name = f"SpiderwebMouseWatch{id(self)}"
        wc = _WNDCLASS()
        wc.proc = ctypes.cast(u.DefWindowProcW, ctypes.c_void_p).value
        wc.instance = inst
        wc.name = name
        hwnd = None
        try:
            if not u.RegisterClassW(ctypes.byref(wc)):
                return
            hwnd = u.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, W.HWND(-3), None, inst, None)  # HWND_MESSAGE
            if not hwnd:
                return
            devs = (_DEVICE * 2)(_DEVICE(1, 2, RIDEV_INPUTSINK, hwnd), _DEVICE(1, 6, RIDEV_INPUTSINK, hwnd))
            if not u.RegisterRawInputDevices(devs, 2, ctypes.sizeof(_DEVICE)):
                return
            self.on = True
            self._ready.set()
            msg = _MSG()
            buf = _INPUT()
            size = ctypes.c_uint()
            head = ctypes.sizeof(_HEADER)
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_INPUT:
                    size.value = ctypes.sizeof(buf)
                    if u.GetRawInputData(W.HANDLE(msg.lparam), RID_INPUT, ctypes.byref(buf), ctypes.byref(size),
                                         head) not in (0, 0xFFFFFFFF):
                        self._input(buf, msg)
                u.DispatchMessageW(ctypes.byref(msg))  # (WM_INPUT: DefWindowProc lets go of its data)
        finally:
            self.on = False
            self._ready.set()
            if hwnd:
                gone = (_DEVICE * 2)(_DEVICE(1, 2, RIDEV_REMOVE, None), _DEVICE(1, 6, RIDEV_REMOVE, None))
                u.RegisterRawInputDevices(gone, 2, ctypes.sizeof(_DEVICE))
                u.DestroyWindow(hwnd)
            u.UnregisterClassW(name, inst)

    def _input(self, buf, msg):
        if buf.header.type == RIM_TYPEKEYBOARD:
            kb = buf.data.keyboard
            for keys, held in ((CTRL_KEYS, self._ctrl), (SHIFT_KEYS, self._shift)):
                if kb.vkey in keys:
                    side = kb.make, kb.flags & 2  # (left / right: the E0 flag)
                    if kb.flags & KEY_BREAK:
                        held.discard(side)
                    else:
                        held.add(side)
        elif buf.header.type == RIM_TYPEMOUSE:
            f = buf.data.mouse.button_flags
            for bit, pressed in ((LEFT_DOWN, True), (LEFT_UP, False)):
                if f & bit:
                    self.events.append((pressed, msg.pt.x, msg.pt.y, msg.time, bool(self._ctrl),
                                        bool(self._shift)))
