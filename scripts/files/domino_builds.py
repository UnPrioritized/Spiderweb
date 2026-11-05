"""Domino versions Spiderweb has no hand-made table for: their reading spots found BY NAME (PLAN L1, 2026-11-06).

Domino.exe carries its class names (the compiler's type information). The piano roll view, the ruler, the main
window and a note are found by those names in the program file (CPianoRollWnd, CTrackSheet, CMainFrame,
CEventNote), then the spots where the running Domino keeps them are found by looking through its global data for
the one pointer to each. Measured on Domino 1.41b, 1.42, 1.43, 1.43 + English + dominohook and 1.44 (all 32-bit,
dev/scratch/dx_scan.py): one spot each, the same as the hand-made 1.43 table, and 1.43's places inside each part
read right in all of them (FAMILY_32). 64-bit Domino (1.45 builds): not yet (None = "not known").
Read-only, like the rest. Anything that doesn't add up = None: never guessed."""

import struct
from dataclasses import replace

# places inside each part, the same in every 32-bit Domino measured (1.41b - 1.44)
FAMILY_32 = dict(top_row=0xB8, row_px=0x1CC, beat_px=0x1D0, left=0x234, cursor=0x190, song=0x184, ppq_at=0xC,
                 tracks_at=0x14, track_now=0x24, track_ch=0x8, events_at=0x18, undo_at=0x188, path_at=0xA8,
                 doc_path_at=0x18C)
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


def from_file(path, build_type):
    """A Build for a Domino.exe without a hand-made table (its slots still 0: found in the running Domino by
    find_slots), or None when it isn't a Domino this can read."""
    try:
        with open(path, "rb") as f:
            pe = _PE(f.read())
    except (OSError, ValueError, struct.error):
        return None
    if pe.x64 or pe.moves or pe.base != 0x400000:
        return None  # (64-bit / moving Domino: PLAN L2)
    got = {k: vtable_32(pe, n) for k, n in CLASSES.items()}
    if None in got.values():
        return None
    data, dat = pe.section(".data")
    ver = pe.version() or "?"
    return build_type(f"Domino {ver}", p_slot=0, h_slot=0, c_slot=0, data_at=dat, data_size=len(data) or 0,
                      **got, **FAMILY_32)


def find_slots(build, read):
    """The build with the spots where this running Domino keeps its piano roll view, ruler and main window, or
    None (not found / more than one view / numbers that don't add up). read(addr, n) -> bytes or None."""
    raw = read(build.data_at, build.data_size) or b""
    if len(raw) < 1024:
        return None
    first = {}
    hits = {k: [] for k in SLOTS}
    want = {getattr(build, v): k for k, v in SLOTS.items()}
    lo, hi = build.data_at - 0x200000, build.data_at + 0x400000  # (the program itself: not an object)
    for o in range(0, len(raw) - 3, 4):
        p = struct.unpack_from("<I", raw, o)[0]
        if p < 0x10000 or lo <= p < hi or p & 3:
            continue
        if p not in first:
            b = read(p, 4)
            first[p] = struct.unpack("<I", b)[0] if b and len(b) == 4 else None
        k = want.get(first[p])
        if k:
            hits[k].append((build.data_at + o, p))
    if len(hits["p_slot"]) != 1 or len(hits["h_slot"]) != 1 or not hits["c_slot"]:
        return None
    if len({p for _, p in hits["c_slot"]}) != 1:
        return None  # (two main windows: can't be)
    out = replace(build, p_slot=hits["p_slot"][0][0], h_slot=hits["h_slot"][0][0], c_slot=hits["c_slot"][-1][0])
    return out if adds_up(out, read) else None


def adds_up(b, read):
    """The song read through these spots looks like a song: a PPQ, 1+ tracks (Conductor first), channels 0-15."""
    def u32(a):
        x = read(a, 4)
        return struct.unpack("<I", x)[0] if x and len(x) == 4 else None
    c = u32(b.c_slot)
    s = u32(c + b.song) if c else None
    if not s:
        return False
    ppq, arr, n = u32(s + b.ppq_at), u32(s + b.tracks_at), u32(s + b.tracks_at + 4)
    if ppq is None or not 1 <= ppq & 0xFFFF <= 65535 or not arr or not n or not 1 <= n <= 65535:
        return False
    t = u32(arr)
    ch = read(t + b.track_ch, 1) if t else None
    return bool(ch) and ch[0] < 64
