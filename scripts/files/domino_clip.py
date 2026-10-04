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
tracks together. The start dropdown (DOMINO_STARTS) picks whether the empty lead before the first note (from
the bar line) comes along, both ways.
"""

import struct
import zlib

import numpy as np

from files import clipboard
from files.lang import tr

FORMAT = "MidiPortalSequence"
MAGIC = b"PortalSequenceData"
LONGEST = 2 ** 31 - 1  # pasted notes are saved as 32-bit ticks (custom.pack_notes): a note ending later = damaged data

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


DOMINO_STARTS = [  # (saved value, dropdown text): where copied / pasted notes start (app.domino_start)
    ("note", tr("domino_clip.first_note_at_tick_0")),
    ("bar", tr("domino_clip.from_the_bar_line")),
]


def item(tag, body):
    return struct.pack("<HI", tag, len(body)) + body


def clip_data(notes, ppq, bar, start="bar"):
    """notes: (start, end, pitch, velocity, slot, ...) rows -> the clipboard bytes. One track per slot (channel)
    that has notes, in slot order, packed together (no empty tracks); Domino fills tracks downwards from the
    highlighted one. start "bar": the copy starts at the bar line at or before the first note (so the notes keep
    their place in the bar) and runs to the bar line after the last; "note": it starts on the first note and ends
    with the last, so the first note pastes right at Domino's cursor. Every track shares that stretch."""
    if start == "note":
        first = int(notes[:, 0].min())
        length = max(int(notes[:, 1].max()) - first, 1)
    else:
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
    return clipboard.put(clipboard.registered(FORMAT), raw)


def get_from_clipboard():
    """The clipboard's FORMAT bytes, b"" if it has none, or None if the clipboard was busy."""
    return clipboard.get(clipboard.registered(FORMAT))


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
    """Clipboard bytes -> (notes, ppq): (tick, gate, key, velocity, track) rows of every track's notes, ticks
    counted from the start of the copied stretch, track = which copied track (0 = the first); ppq = the PPQ it was
    copied at (None if missing). ValueError if raw isn't Domino's data."""
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
        raise ValueError(tr("domino_clip.not_domino_s_data"))
    try:
        data = zlib.decompress(raw[len(MAGIC) + 4:])
    except zlib.error:
        raise ValueError(tr("domino_clip.domino_s_data_is_damaged")) from None
    runs, odd, ppq, track = [], [], None, -1
    for tag, body in items(data):
        if tag == 1002 and len(body) == 2:
            ppq = struct.unpack("<H", body)[0]
        if tag != 1003:
            continue
        track += 1
        i = 0
        while i + 6 <= len(body):
            t, n = struct.unpack_from("<HI", body, i)
            if t == 2001 and n == NOTE.itemsize - 6:  # notes in the usual layout: all of them at once
                got, i = note_run(body, i)
                runs += [(r, track) for r in got]
                if got and sum(map(len, got)):
                    continue
            if i + 6 + n > len(body):
                break
            if t == 2001:  # a note in some other layout; anything else (controllers, settings) is skipped
                f = dict(items(body[i + 6:i + 6 + n]))
                if len(f.get(1001, b"")) == 4 and len(f.get(2001, b"")) == 1 and len(f.get(2003, b"")) == 4:
                    vel = f.get(2002, b"")[:1] or bytes([100])
                    odd.append((int.from_bytes(f[1001], "little"), int.from_bytes(f[2003], "little"),
                                f[2001][0], vel[0], track))
            i += 6 + n
    rows = np.concatenate([np.column_stack([r["tick"], r["gate"], r["key"], r["vel"],
                                            np.full(len(r), k)]).astype(np.int64) for r, k in runs]
                          + [np.array(odd, np.int64).reshape(-1, 5)])
    rows = rows[rows[:, 2] <= 127]
    if len(rows) and int((rows[:, 0] + rows[:, 1]).max()) > LONGEST:
        raise ValueError(tr("domino_clip.domino_s_data_is_damaged"))
    rows[:, 1] = np.maximum(rows[:, 1], 1)
    rows[:, 3] = np.clip(rows[:, 3], 1, 127)
    return rows, ppq
