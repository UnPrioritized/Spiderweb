"""Copy to Domino: notes on the clipboard in the format Domino's own Ctrl+C uses, so Ctrl+V in Domino pastes them.

The clipboard format is "MidiPortalSequence": b"PortalSequenceData", the unpacked size (u32), then zlib data.
Unpacked it's items of [tag u16][length u32][data], some holding more items: song settings, one track per
copied track (settings, the notes, the copied stretch's length, more settings), more song settings.
A note = item 2001 holding 1001 (start tick, u32), 2001 (key, u8), 2002 (velocity, u8), 2003 (gate, u32).
Ticks count from the start of the copied stretch; Domino pastes that start at its play cursor and doesn't
convert ticks to its own PPQ (the user matches the PPQ). The track's channel isn't in the data: the tracks
go into the highlighted track and the ones below it (tracks past the last one are dropped). Everything but the notes, the PPQ and the length is copied from a real
Domino copy (the copyright text left empty).

Paste from Domino reads the same format back: only the notes (controller and other events are skipped), all
tracks together.
"""

import ctypes
import struct
import zlib
from ctypes import wintypes

import numpy as np

FORMAT = "MidiPortalSequence"
MAGIC = b"PortalSequenceData"

SONG_START = bytes.fromhex("e80300000000e90300000000")  # 1000 (empty), 1001 = copyright text (empty)
SONG_REST = bytes.fromhex(  # after 1002 = PPQ
    "ef030400000030000000f1030400000000000000f403080000000000000000000000f5030400000000000000f6030400000000"
    "000000fb0300000000fc030400000010000000fd030100000001fe030100000001ff03040000001100000000040100000001")
TRACK_HEAD = bytes.fromhex(
    "e803020000000000e9030100000000ea0300000000eb030100000000ec030100000000f003010000003cf103110000004765"
    "6e6572616c204d494449204472756df303020000000000f4030400000000000000f8030400000064000000f9030400000000"
    "000000f5030100000001f603020000000100f7030100000001fa0301000000fffb030400000001000000fc03100000000000"
    "0000000000000000000000000000fd030100000000fe03010000007f")
TRACK_TAIL = bytes.fromhex(
    "ed030400000032000000ee030100000064ef0304000000e0010000f2030e000000e8030100000000e9030100000000")
SONG_TAIL = bytes.fromhex(
    "ee0300000000f0031a000000e80300000000e9030400000001000000ea030400000001000000f90342000000640001000000"
    "0065000100000000660001000000006700040000006400000068000100000000690004000000640000006a000c0000000505"
    "05050505050505050505")

# one note = 40 bytes: the note item's tag and length, then its four items (tag, length, value)
NOTE = np.dtype([("tag", "<u2"), ("len", "<u4"),
                 ("t1", "<u2"), ("l1", "<u4"), ("tick", "<u4"),
                 ("t2", "<u2"), ("l2", "<u4"), ("key", "u1"),
                 ("t3", "<u2"), ("l3", "<u4"), ("vel", "u1"),
                 ("t4", "<u2"), ("l4", "<u4"), ("gate", "<u4")])


def item(tag, body):
    return struct.pack("<HI", tag, len(body)) + body


def clip_data(notes, ppq, bar):
    """notes: (start, end, pitch, velocity, slot, ...) rows -> the clipboard bytes. One track per slot (channel)
    that has notes, in slot order, packed together (no empty tracks); Domino fills tracks downwards from the
    highlighted one. The copy starts at the bar line at or before the first note (so the notes keep their place
    in the bar) and runs to the bar line after the last; every track shares that stretch."""
    first = int(notes[:, 0].min()) // bar * bar
    length = max(-(-(int(notes[:, 1].max()) - first) // bar) * bar, bar)
    end = item(2009, item(1001, struct.pack("<I", length)))
    tracks = b""
    for slot in np.unique(notes[:, 4]).tolist():
        mine = notes[notes[:, 4] == slot]
        mine = mine[np.lexsort((mine[:, 2], mine[:, 0]))]
        rows = np.zeros(len(mine), NOTE)
        rows["tag"], rows["len"] = 2001, NOTE.itemsize - 6
        rows["t1"], rows["l1"], rows["tick"] = 1001, 4, mine[:, 0] - first
        rows["t2"], rows["l2"], rows["key"] = 2001, 1, mine[:, 2]
        rows["t3"], rows["l3"], rows["vel"] = 2002, 1, mine[:, 3]
        rows["t4"], rows["l4"], rows["gate"] = 2003, 4, mine[:, 1] - mine[:, 0]
        tracks += item(1003, TRACK_HEAD + rows.tobytes() + end + TRACK_TAIL)
    data = SONG_START + item(1002, struct.pack("<H", ppq)) + SONG_REST + tracks + SONG_TAIL
    return MAGIC + struct.pack("<I", len(data)) + zlib.compress(data)


def put_on_clipboard(raw):
    """raw bytes -> the clipboard as FORMAT (replaces what's there). Returns False if the clipboard was busy."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    fmt = user32.RegisterClipboardFormatW(FORMAT)
    h = kernel32.GlobalAlloc(0x0002, len(raw))  # GMEM_MOVEABLE
    if not h:
        raise MemoryError("not enough memory for the clipboard")
    ctypes.memmove(kernel32.GlobalLock(h), raw, len(raw))
    kernel32.GlobalUnlock(h)
    for _ in range(10):  # another program may have it open for a moment
        if user32.OpenClipboard(None):
            break
        kernel32.Sleep(20)
    else:
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


def get_from_clipboard():
    """The clipboard's FORMAT bytes, b"" if it has none, or None if the clipboard was busy."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    fmt = user32.RegisterClipboardFormatW(FORMAT)
    for _ in range(10):  # another program may have it open for a moment
        if user32.OpenClipboard(None):
            break
        kernel32.Sleep(20)
    else:
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


def items(data, i=0):
    """(tag, body) of each item in data from byte i; stops at anything that doesn't fit."""
    while i + 6 <= len(data):
        tag, n = struct.unpack_from("<HI", data, i)
        if i + 6 + n > len(data):
            return
        yield tag, data[i + 6:i + 6 + n]
        i += 6 + n


def note_run(body, i):
    """The notes written one after another in NOTE's layout from byte i of body (at least the first one is)."""
    got, n = [], 64
    while i + NOTE.itemsize <= len(body):
        recs = np.frombuffer(body, NOTE, count=min(n, (len(body) - i) // NOTE.itemsize), offset=i)
        ok = ((recs["tag"] == 2001) & (recs["len"] == NOTE.itemsize - 6) & (recs["t1"] == 1001) & (recs["l1"] == 4)
              & (recs["t2"] == 2001) & (recs["l2"] == 1) & (recs["t3"] == 2002) & (recs["l3"] == 1)
              & (recs["t4"] == 2003) & (recs["l4"] == 4))
        run = len(recs) if ok.all() else int(ok.argmin())
        got.append(recs[:run])
        i += run * NOTE.itemsize
        if run < len(recs):
            break
        n *= 2  # (checked in growing pieces: something else between the notes stops a run early)
    return got, i


def read_notes(raw):
    """Clipboard bytes -> (notes, ppq): (tick, gate, key, velocity) rows of every track's notes, ticks counted
    from the start of the copied stretch; ppq = the PPQ it was copied at (None if missing). ValueError if raw
    isn't Domino's data."""
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
        raise ValueError("not Domino's data")
    try:
        data = zlib.decompress(raw[len(MAGIC) + 4:])
    except zlib.error:
        raise ValueError("Domino's data is damaged") from None
    runs, odd, ppq = [], [], None
    for tag, body in items(data):
        if tag == 1002 and len(body) == 2:
            ppq = struct.unpack("<H", body)[0]
        if tag != 1003:
            continue
        i = 0
        while i + 6 <= len(body):
            t, n = struct.unpack_from("<HI", body, i)
            if t == 2001 and n == NOTE.itemsize - 6:  # notes in the usual layout: all of them at once
                got, i = note_run(body, i)
                runs += got
                if got and sum(map(len, got)):
                    continue
            if i + 6 + n > len(body):
                break
            if t == 2001:  # a note in some other layout; anything else (controllers, settings) is skipped
                f = dict(items(body[i + 6:i + 6 + n]))
                if len(f.get(1001, b"")) == 4 and len(f.get(2001, b"")) == 1 and len(f.get(2003, b"")) == 4:
                    vel = f.get(2002, b"")[:1] or bytes([100])
                    odd.append((int.from_bytes(f[1001], "little"), int.from_bytes(f[2003], "little"),
                                f[2001][0], vel[0]))
            i += 6 + n
    rows = np.concatenate([np.column_stack([r["tick"], r["gate"], r["key"], r["vel"]]).astype(np.int64)
                           for r in runs] + [np.array(odd, np.int64).reshape(-1, 4)])
    rows = rows[rows[:, 2] <= 127]
    rows[:, 1] = np.maximum(rows[:, 1], 1)
    rows[:, 3] = np.clip(rows[:, 3], 1, 127)
    return rows, ppq
