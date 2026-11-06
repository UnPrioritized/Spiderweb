"""Domino versions Spiderweb has no hand-made table for: their reading spots found BY NAME (PLAN L1, 2026-11-06).

Domino.exe carries its class names (the compiler's type information). The piano roll view, the ruler, the main
window and a note are found by those names in the program file (CPianoRollWnd, CTrackSheet, CMainFrame,
CEventNote), then the spots where the running Domino keeps them are found by looking through its global data for
the one pointer to each. Measured on Domino 1.41b, 1.42, 1.43, 1.43 + English + dominohook and 1.44 (all 32-bit,
dev/scratch/dx_scan.py): one spot each, the same as the hand-made 1.43 table, and 1.43's places inside each part
read right in all of them (FAMILY_32). 64-bit Domino (1.45 dev002 = the 256 keys mod's, 1.45 dev009-en nightly,
PLAN L2 / L3, dev/scratch/dx64_*.py): the same classes, loaded somewhere else each start (domino_view.with_slots
moves the addresses), 8-byte pointers, places in FAMILY_64 (both builds alike); the 256 keys mod says its 256 rows
itself (rows_at). 32-bit 1.45 (dev009 x86) moves too, with its own places (FAMILY_32_145).
Read-only, like the rest. Anything that doesn't add up = None: never guessed."""

import struct
from dataclasses import replace

# places inside each part, the same in every 32-bit Domino measured (1.41b - 1.44)
FAMILY_32 = dict(top_row=0xB8, row_px=0x1CC, beat_px=0x1D0, left=0x234, cursor=0x190, song=0x184, ppq_at=0xC,
                 tracks_at=0x14, track_now=0x24, track_ch=0x8, events_at=0x18, undo_at=0x188, path_at=0xA8,
                 doc_path_at=0x18C)
# 32-bit 1.45 (dev009 x86, loads elsewhere each start; dev/scratch/dk145_probe.py, 2026-11-07): the view, ruler and
# main window keep their numbers elsewhere (rows_at as in 64-bit: 0x10 before the top row); tracks and notes as in
# FAMILY_32, the song's .mid import path at [[S+0xA0]+4] (dev/scratch/dk145_path.py)
FAMILY_32_145 = dict(FAMILY_32, top_row=0x144, row_px=0x2EC, beat_px=0x2F0, left=0x3DC, song=0x284, undo_at=0x288,
                     doc_path_at=0x28C, cursor=0x290, rows_at=0x134, path_at=0xA0)
# the same in both 64-bit builds measured (1.45 dev002, 1.45 dev009-en); the .mid import path [[S+0x140]+8]
# (found 2026-11-06, dev/scratch/rm_midpath.py)
FAMILY_64 = dict(top_row=0x228, row_px=0x4D0, beat_px=0x4D4, left=0x6A8, cursor=0x448, song=0x430, ppq_at=0x18,
                 tracks_at=0x28, track_now=0x48, track_ch=0xC, events_at=0x28, undo_at=0x438, undo_n=0x20,
                 path_at=0x140, path_obj=8, doc_path_at=0x440, n_tick=8, n_sel=0xC, n_key=0x10, n_vel=0x11, n_gate=0x14,
                 rows_at=0x218, ptr=8)
CLASSES = {"p_vtable": b".?AVCPianoRollWnd@@", "h_vtable": b".?AVCTrackSheet@@", "c_vtable": b".?AVCMainFrame@@",
           "note_vt": b".?AVCEventNote@@"}
SLOTS = {"p_slot": "p_vtable", "h_slot": "h_vtable", "c_slot": "c_vtable"}


class _PE:
    """The bits of a Windows program file needed here (sections, image base, version text)."""

    def __init__(self, data):
        self.d = data
        if data[:2] != b"MZ":
            raise ValueError("not a program")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError("not a program")
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt = pe + 24
        self.x64 = struct.unpack_from("<H", data, opt)[0] == 0x20B
        if self.x64:
            self.base = struct.unpack_from("<Q", data, opt + 24)[0]
            self.moves = bool(struct.unpack_from("<H", data, opt + 70)[0] & 0x40)
        else:
            self.base = struct.unpack_from("<I", data, opt + 28)[0]
            self.moves = bool(struct.unpack_from("<H", data, opt + 70)[0] & 0x40)  # (DYNAMIC_BASE: loads elsewhere)
        self.size = struct.unpack_from("<I", data, opt + 56)[0]  # (SizeOfImage: the whole program as loaded)
        first = opt + struct.unpack_from("<H", data, pe + 20)[0]
        self.secs = {}
        for i in range(nsec):
            s = first + 40 * i
            name = data[s:s + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, s + 8)
            self.secs.setdefault(name, (va, max(vsize, rsize), raw, rsize))

    def section(self, name):
        """(bytes as in the file, its address when loaded) or (b"", 0)."""
        if name not in self.secs:
            return b"", 0
        va, _, raw, rsize = self.secs[name]
        return self.d[raw:raw + rsize], self.base + va

    def version(self):
        k = "FileVersion".encode("utf-16-le") + b"\0\0"
        i = self.d.find(k)
        if i < 0:
            return ""
        j = i + len(k)
        while self.d[j:j + 2] == b"\0\0":
            j += 2
        end = j
        while end + 1 < len(self.d) and self.d[end:end + 2] != b"\0\0":
            end += 2
        return self.d[j:end].decode("utf-16-le", "replace").strip()


def vtable_32(pe, name):
    """The address of a class's main vtable (32-bit program) found by its name, or None (missing / more than one)."""
    rdata, at = pe.section(".rdata")
    data, dat = pe.section(".data")
    name += b"\0"
    i = data.find(name)  # (the type descriptor: vtable pointer, spare, then the name)
    if i < 8 or data.find(name, i + 1) >= 0:
        return None
    td = struct.pack("<I", dat + i - 8)
    found = []
    j = rdata.find(td)
    while j >= 0:
        if j >= 12 and j % 4 == 0 and rdata[j - 12:j - 4] == b"\0" * 8:  # (locator: signature 0, offset 0)
            col = struct.pack("<I", at + j - 12)
            m = rdata.find(col)
            while m >= 0:
                if m % 4 == 0:
                    found.append(at + m + 4)
                m = rdata.find(col, m + 1)
        j = rdata.find(td, j + 1)
    return found[0] if len(found) == 1 else None


def vtable_64(pe, name):
    """The address (at the file's own load address) of a class's main vtable in a 64-bit program, or None. 64-bit
    type info counts in offsets from the load address: the type descriptor = vtable pointer, spare, name (8 + 8);
    the locator = signature 1, offset 0, ..., its type descriptor's offset at + 12, its own offset at + 20."""
    rdata, at = pe.section(".rdata")
    data, dat = pe.section(".data")
    name += b"\0"
    i = data.find(name)
    if i < 16 or data.find(name, i + 1) >= 0:
        return None
    td = struct.pack("<I", dat - pe.base + i - 16)
    found = []
    j = rdata.find(td)
    while j >= 0:
        c = j - 12
        if c >= 0 and c % 4 == 0 and rdata[c:c + 8] == b"\1\0\0\0\0\0\0\0" \
                and struct.unpack_from("<I", rdata, c + 20)[0] == at - pe.base + c:
            col = struct.pack("<Q", at + c)
            m = rdata.find(col)
            while m >= 0:
                if m % 8 == 0:
                    found.append(at + m + 8)
                m = rdata.find(col, m + 1)
        j = rdata.find(td, j + 1)
    return found[0] if len(found) == 1 else None


def from_file(path, build_type):
    """A Build for a Domino.exe without a hand-made table (its slots still 0: found in the running Domino by
    find_slots; a 64-bit one's addresses moved there first), or None when it isn't a Domino this can read."""
    try:
        return _from_file(path, build_type)
    except Exception:  # (an odd program file: "not a Domino we can read", never an error loop, hunt 2026-11-06)
        return None


def _from_file(path, build_type):
    try:
        with open(path, "rb") as f:
            pe = _PE(f.read())
    except (OSError, ValueError, struct.error):
        return None
    if pe.x64:
        family, find, extra = FAMILY_64, vtable_64, {"file_base": pe.base, "image_size": pe.size}
    elif pe.moves:  # (1.45 dev009 x86: loads elsewhere each start, moved like a 64-bit one)
        family, find, extra = FAMILY_32_145, vtable_32, {"file_base": pe.base, "image_size": pe.size}
    else:
        family, find, extra = FAMILY_32, vtable_32, {}
    got = {k: find(pe, n) for k, n in CLASSES.items()}
    if None in got.values():
        return None
    _, dat = pe.section(".data")
    size = pe.secs.get(".data", (0, 0))[1]  # (as loaded: the part zero in the file too; 1.45's slots are there)
    ver = pe.version() or "?"
    return build_type(f"Domino {ver}{' (64-bit)' if pe.x64 else ''}", p_slot=0, h_slot=0, c_slot=0, data_at=dat,
                      data_size=size, **got, **family, **extra)


def find_slots(build, read, check=True):
    """The build with the spots where this running Domino keeps its piano roll view, ruler and main window, or
    None (not found / more than one view / numbers that don't add up). read(addr, n) -> bytes or None. check=False:
    the spots found without adds_up (domino_view asks it apart: "Try anyway" may use them)."""
    raw = read(build.data_at, build.data_size) or b""
    if len(raw) < 1024:
        return None
    first = {}
    hits = {k: [] for k in SLOTS}
    want = {getattr(build, v): k for k, v in SLOTS.items()}
    lo, hi = build.data_at - 0x800000, build.data_at + 0x800000  # (the program itself: not an object)
    if build.file_base and build.image_size:  # (a moving one, exactly: 1.45 x86 may load so low that its objects
        lo, hi = build.file_base, build.file_base + build.image_size  # sit within the rough range above)
    size, fmt = build.ptr, "<Q" if build.ptr == 8 else "<I"
    for o in range(0, len(raw) - size + 1, size):
        p = struct.unpack_from(fmt, raw, o)[0]
        if p < 0x10000 or lo <= p < hi or p & (size - 1) or p >= 1 << 47:
            continue
        if p not in first:
            b = read(p, size)
            first[p] = struct.unpack(fmt, b)[0] if b and len(b) == size else None
        k = want.get(first[p])
        if k:
            hits[k].append((build.data_at + o, p))
    if len(hits["p_slot"]) != 1 or len(hits["h_slot"]) != 1 or not hits["c_slot"]:
        return None
    if len({p for _, p in hits["c_slot"]}) != 1:
        return None  # (two main windows: can't be)
    out = replace(build, p_slot=hits["p_slot"][0][0], h_slot=hits["h_slot"][0][0], c_slot=hits["c_slot"][-1][0])
    return out if not check or adds_up(out, read) else None


def adds_up(b, read):
    """The song read through these spots looks like a song: a PPQ, 1+ tracks (Conductor first), channels 0-15."""
    def u32(a):
        x = read(a, 4)
        return struct.unpack("<I", x)[0] if x and len(x) == 4 else None

    def ptr(a):
        x = read(a, b.ptr)
        return int.from_bytes(x, "little") if x and len(x) == b.ptr else None
    c = ptr(b.c_slot)
    s = ptr(c + b.song) if c else None
    if not s:
        return False
    ppq, arr, n = u32(s + b.ppq_at), ptr(s + b.tracks_at), u32(s + b.tracks_at + b.ptr)
    if ppq is None or not 1 <= ppq <= 65535 or not arr or not n or not 1 <= n <= 65535:  # (the whole number, as
        return False  # domino_view.song_place reads it: one test passing and the other failing = every send refused)
    if b.undo_at:  # (hunt 2026-11-06: the undo count is read too, every look)
        u = ptr(c + b.undo_at)
        depth = u32(u + b.undo_n) if u else None
        if depth is None or depth > 10_000_000:
            return False
    now, cursor = u32(s + b.track_now), u32(c + b.cursor)
    if now is None or not 0 <= now < n or cursor is None or cursor >= 2 ** 31:
        return False
    # (PLAN L4: a new Domino with things moved must fail here, never be read wrong) the view's numbers ...
    p, h = ptr(b.p_slot), ptr(b.h_slot)
    nums = [u32(p + b.top_row), u32(p + b.row_px), u32(p + b.beat_px)] if p else [None]
    left = u32(h + b.left) if h else None
    if None in nums or left is None or not (nums[0] < 256 and 1 <= nums[1] <= 200 and 1 <= nums[2] <= 10000
                                            and left < 2 ** 31):
        return False
    # ... and every track (up to 64): a channel byte, an event list whose events are objects of the program's
    lo, hi = b.data_at - 0x1000000, b.data_at + 0x1000000
    tracks = read(arr, b.ptr * min(n, 64)) or b""
    for i in range(min(n, 64)):
        t = int.from_bytes(tracks[i * b.ptr:(i + 1) * b.ptr], "little") if len(tracks) >= (i + 1) * b.ptr else 0
        ch = read(t + b.track_ch, 1) if t else None
        if not ch or ch[0] >= 64:
            return False
        ev, m = ptr(t + b.events_at), u32(t + b.events_at + b.ptr)
        if m is None or m > 50_000_000 or (m and not ev):
            return False
        last = -1
        for j in range(min(m, 4)):
            e = ptr(ev + j * b.ptr)
            vt = ptr(e) if e else None
            if vt is None or not lo <= vt < hi:
                return False
            if vt == b.note_vt:  # (hunt 2026-11-06: the note's own layout: a Domino keeping tick / velocity /
                last = _note_tick(b, read, e, last)  # gate elsewhere must fail, never be misread)
                if last is None:
                    return False
    return True


def _note_tick(b, read, e, last):
    """A note read through the build's places: its tick when it looks like a note (a tick never before the note
    before it, velocity 0-127, a gate), else None."""
    raw = read(e, b.n_gate + 4)
    if not raw or len(raw) < b.n_gate + 4:
        return None
    tick = struct.unpack_from("<I", raw, b.n_tick)[0]
    gate = struct.unpack_from("<I", raw, b.n_gate)[0]
    ok = last <= tick < 2 ** 31 and raw[b.n_vel] <= 127 and gate < 2 ** 31
    return tick if ok else None
