"""Files dragged in from Explorer, onto a whole window (the user's own kestrelGUI dropfiles.py, made for Spiderweb).

Tk has no drag and drop of its own, so this registers an OLE IDropTarget through ctypes. OLE rather than
WM_DROPFILES because it asks while the files are still being dragged: a drag holding nothing usable gets the no-drop
cursor before anything is let go.

The target sits on the window's frame. OLE looks for one from the window under the cursor up through its parents,
so every widget inside takes the drop without registering anything of its own.

OLE calls the target from inside Tk's event loop, while _tkinter has let go of Python and holds Tcl's lock. Calling
into Tk from there (even after) leaves _tkinter without its thread state, and the next Tk callback aborts the
process with no message. So the callbacks here touch no widget: a drop goes on a queue, which an after poll on Tk's
side hands to on_drop.

Windows only. Elsewhere accept_drops returns False, and there, or where OLE refuses the registration, the window
simply takes no drops.
"""

import ctypes
import queue

from files.system import WINDOWS, wintypes

DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1
CF_HDROP = 15
DVASPECT_CONTENT = 1
TYMED_HGLOBAL = 1
S_OK = 0
E_NOINTERFACE = 0x80004002 - 2 ** 32
POLL_MS = 100

# IUnknown and IDropTarget: the only two interfaces this object answers to.
_IIDS = {bytes.fromhex("00000000" "0000" "0000" "c000000000000046"),
         bytes.fromhex("22010000" "0000" "0000" "c000000000000046")}

# Every target registered, kept alive for as long as the process: OLE holds raw pointers into these, and a collected
# one would crash the next drag.
_alive = []


class _FORMATETC(ctypes.Structure):
    _fields_ = [("cfFormat", wintypes.WORD), ("ptd", ctypes.c_void_p), ("dwAspect", wintypes.DWORD),
                ("lindex", wintypes.LONG), ("tymed", wintypes.DWORD)]


class _STGMEDIUM(ctypes.Structure):
    _fields_ = [("tymed", wintypes.DWORD), ("hGlobal", ctypes.c_void_p), ("pUnkForRelease", ctypes.c_void_p)]


if WINDOWS:
    _F = ctypes.WINFUNCTYPE
    _HR = ctypes.c_long
    _PDW = ctypes.POINTER(wintypes.DWORD)
    # POINTL is two LONGs passed by value: 8 bytes, x in the low half (x64 hands it over as one 64-bit integer;
    # 32-bit Windows pushes the same 8 bytes on the stack, so one c_int64 reads it too)
    _PT = ctypes.c_int64
    _QueryInterface = _F(_HR, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
    _AddRef = _F(wintypes.ULONG, ctypes.c_void_p)
    _DragEnter = _F(_HR, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, _PT, _PDW)
    _DragOver = _F(_HR, ctypes.c_void_p, wintypes.DWORD, _PT, _PDW)
    _DragLeave = _F(_HR, ctypes.c_void_p)
    _GetData = _F(_HR, ctypes.c_void_p, ctypes.POINTER(_FORMATETC), ctypes.POINTER(_STGMEDIUM))

    class _Vtbl(ctypes.Structure):
        _fields_ = [("QueryInterface", _QueryInterface), ("AddRef", _AddRef), ("Release", _AddRef),
                    ("DragEnter", _DragEnter), ("DragOver", _DragOver), ("DragLeave", _DragLeave),
                    ("Drop", _DragEnter)]

    class _Target(ctypes.Structure):
        _fields_ = [("vtbl", ctypes.POINTER(_Vtbl))]


def _dragged_paths(data_object):
    """The file paths an IDataObject carries, or [] for anything else."""
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    ole32.ReleaseStgMedium.argtypes = [ctypes.POINTER(_STGMEDIUM)]
    vtbl = ctypes.cast(data_object, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    get_data = _GetData(vtbl[3])
    fmt = _FORMATETC(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
    medium = _STGMEDIUM()
    if get_data(data_object, ctypes.byref(fmt), ctypes.byref(medium)) != S_OK:
        return []
    try:
        paths = []
        for i in range(shell32.DragQueryFileW(medium.hGlobal, 0xFFFFFFFF, None, 0)):
            size = shell32.DragQueryFileW(medium.hGlobal, i, None, 0) + 1
            buf = ctypes.create_unicode_buffer(size)
            shell32.DragQueryFileW(medium.hGlobal, i, buf, size)
            paths.append(buf.value)
        return paths
    finally:
        ole32.ReleaseStgMedium(ctypes.byref(medium))


def accept_drops(window, wanted, on_drop):
    """Lets files be dropped anywhere on the toplevel window. wanted(paths) returns the ones the window can use;
    with none, the drag is refused. It runs inside OLE's call, so it must not touch Tk. on_drop(paths) gets that
    list later, on Tk's own event loop. Returns the poll's starter (tests cancel every timer: they call it again),
    or None where drops can't be taken."""
    if not WINDOWS:
        return None
    try:
        ole32 = ctypes.windll.ole32
        ole32.OleInitialize(None)  # (S_FALSE when Tk's thread already has it)
    except (AttributeError, OSError):
        return None
    state = {"paths": []}
    dropped = queue.SimpleQueue()

    def query_interface(this, riid, out):
        if ctypes.string_at(riid, 16) in _IIDS:
            out[0] = this
            return S_OK
        out[0] = None
        return E_NOINTERFACE

    def add_ref(this):
        return 1  # (lives as long as the process)

    def effect(ptr, allowed):
        ptr[0] = DROPEFFECT_COPY if allowed and ptr[0] & DROPEFFECT_COPY else DROPEFFECT_NONE

    def drag_enter(this, data, keys, point, ptr):
        try:
            state["paths"] = wanted(_dragged_paths(data))
        except Exception:
            state["paths"] = []
        effect(ptr, state["paths"])
        return S_OK

    def drag_over(this, keys, point, ptr):
        effect(ptr, state["paths"])
        return S_OK

    def drag_leave(this):
        state["paths"] = []
        return S_OK

    def drop(this, data, keys, point, ptr):
        paths, state["paths"] = state["paths"], []
        effect(ptr, paths)
        if paths:
            dropped.put(paths)  # (no Tk call in here: see the top)
        return S_OK

    vtbl = _Vtbl(_QueryInterface(query_interface), _AddRef(add_ref), _AddRef(add_ref), _DragEnter(drag_enter),
                 _DragOver(drag_over), _DragLeave(drag_leave), _DragEnter(drop))
    target = _Target(ctypes.pointer(vtbl))
    _alive.append((vtbl, target))
    ole32.RegisterDragDrop.argtypes = [wintypes.HWND, ctypes.c_void_p]
    ole32.RevokeDragDrop.argtypes = [wintypes.HWND]
    registered = {}

    def register(event=None):
        # Tk wraps a toplevel in its frame only when first mapped; until then wm frame names the inner window,
        # which drops nothing.
        if (event is not None and event.widget is not window) or registered:
            return
        hwnd = int(window.wm_frame(), 16)
        if ole32.RegisterDragDrop(hwnd, ctypes.byref(target)) == S_OK:
            registered["hwnd"] = hwnd

    polling = {"job": None}

    def poll():
        polling["job"] = None
        if not window.winfo_exists():
            return
        while True:
            try:
                paths = dropped.get_nowait()
            except queue.Empty:
                break
            on_drop(paths)
        start()

    def start():
        if polling["job"] is not None:
            try:
                window.after_cancel(polling["job"])
            except Exception:
                pass
        polling["job"] = window.after(POLL_MS, poll)

    def revoke(event):
        if event.widget is window and registered:
            ole32.RevokeDragDrop(registered.pop("hwnd"))

    if window.winfo_ismapped():
        register()
    else:
        window.bind("<Map>", register, add="+")
    window.bind("<Destroy>", revoke, add="+")
    start()
    return start
