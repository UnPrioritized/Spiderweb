"""Spiderweb Notes: the finished music put on the clipboard beside the shared line (Ctrl+C, with Settings → General
"Ctrl+C also copies as Spiderweb Notes"), for other programs (sequencers) to paste. Unlike the shared line, this
layout stays the same from version to version; it's written up for them in SPIDERWEB-NOTES-FORMAT.md (the repo's
root). Spiderweb writes it, never reads it.

Layout: b"SPWNOTES", version byte, then blocks [type varint][size varint][body] until an End block (0). Notes
records: varint(gap << 6 | flags), key byte, then only what changed since the previous record (flags 1 length,
2 velocity, 4 track, 8 group, 32 channel: 255 = the track's own); flag 16 = a column of keys going up by one, its
count - 1 after the key. Big copies come as several Notes / Events blocks (each starting fresh), each inside a
Zipped block (unzipped size + zlib) when that's smaller."""

import zlib

import numpy as np

NAME = "Spiderweb Notes"  # (the registered clipboard format's name)
TAG = b"SPWNOTES"
VERSION = 1
END, INFO, TRACKS, NOTES, TRACK_COLOURS, ZIPPED, NAMES, CHANNEL_COLOURS = 0, 1, 2, 3, 4, 5, 6, 7
TEMPO, EVENTS, TIME_SIGS, KEY_SIGS = 16, 17, 19, 20
ZIP_LEVEL = 6  # (measured: 9 no smaller, 1 made random sets bigger)
LENGTH, VELOCITY, TRACK, GROUP, COLUMN, CHANNEL = 1, 2, 4, 8, 16, 32
OWN_CHANNEL = 255  # (a note's channel byte: on its track's channel)
PIECE = 1_000_000  # records per Notes / Events block (memory while packing: ~150 bytes a note of the piece)
LONGEST = 2 ** 47  # ticks a copy may span (gap << 6 stays under 2^53, what JavaScript reads exactly)


def varint(v):
    """A whole number >= 0 -> its varint bytes (7 bits a byte, lowest first, top bit = more follow)."""
    out = bytearray()
    while v >= 0x80:
        out.append(v & 0x7F | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def text(s):
    b = s.encode("utf-8")
    return varint(len(b)) + b


def block(kind, body):
    return varint(kind) + varint(len(body)) + body


def _sizes(v):
    """Each value's varint size in bytes."""
    n = np.ones(len(v), np.int64)
    for k in range(1, 10):
        n += v >= np.uint64(1 << (7 * k))
    return n


def _emit(fields):
    """fields: (values uint64, given bool or None = always, "v" varint / "b" byte) per record, in record order ->
    the records' bytes."""
    sizes = []
    for v, given, kind in fields:
        n = _sizes(v) if kind == "v" else np.ones(len(v), np.int64)
        sizes.append(n if given is None else np.where(given, n, 0))
    total = sum(sizes)
    out = np.zeros(int(total.sum()), np.uint8)
    at = np.cumsum(total) - total
    for (v, given, kind), n in zip(fields, sizes):
        if kind == "b":
            m = n > 0
            out[at[m]] = (v[m] & np.uint64(0xFF)).astype(np.uint8)
        else:
            for k in range(int(n.max()) if len(n) else 0):
                m = n > k
                b = ((v[m] >> np.uint64(7 * k)) & np.uint64(0x7F)) | ((n[m] > k + 1).astype(np.uint64) << np.uint64(7))
                out[at[m] + k] = b.astype(np.uint8)
        at += n
    return out.tobytes()


def _u(x):
    return np.asarray(x).astype(np.uint64)


def kept_order(start, length, key, velocity, track, group, channel):
    """The notes that keep the format's rules (length >= 1, keys 0-255, start >= 0), in the order records pack
    best: by start, then track, group, channel, length, velocity, key (a column's keys next to each other). Keys
    sorted in small types (a 10 M-note copy took gigabytes)."""
    rows = np.flatnonzero((length > 0) & (key >= 0) & (key <= 255) & (start >= 0))
    if not len(rows):
        return rows
    sort_keys = (key[rows].astype(np.uint8), velocity[rows].astype(np.uint8), length[rows],
                 channel[rows].astype(np.uint8), group[rows].astype(np.int32), track[rows].astype(np.int32),
                 start[rows])
    order = rows[np.lexsort(sort_keys)]
    del sort_keys, rows
    return order


def records(cols):
    """(start, length, key, velocity, track, group, channel) columns in packing order -> the Notes block's body."""
    start, length, key, velocity, track, group, channel = cols
    same = np.zeros(len(start), bool)  # (a note that carries on the record before as a column)
    same[1:] = ((start[1:] == start[:-1]) & (length[1:] == length[:-1]) & (velocity[1:] == velocity[:-1])
                & (track[1:] == track[:-1]) & (group[1:] == group[:-1]) & (channel[1:] == channel[:-1])
                & (key[1:] == key[:-1] + 1))
    first = ~same
    count = np.bincount(np.cumsum(first) - 1)
    start, length, key, velocity, track, group, channel = (c[first] for c in cols)

    def new(x, start_value=0):  # (given in this record; each block starts fresh at start_value)
        return np.diff(x, prepend=start_value) != 0 if len(x) else np.zeros(0, bool)
    nl, nv, nt, ng = new(length), new(velocity), new(track), new(group)
    nc, column = new(channel, OWN_CHANNEL), count > 1
    nl[:1] = nv[:1] = nt[:1] = ng[:1] = True  # (a block's first record gives them all: clearer for readers)
    head = ((_u(np.diff(start, prepend=0)) << np.uint64(6)) | _u(nl) * np.uint64(LENGTH)
            | _u(nv) * np.uint64(VELOCITY) | _u(nt) * np.uint64(TRACK) | _u(ng) * np.uint64(GROUP)
            | _u(column) * np.uint64(COLUMN) | _u(nc) * np.uint64(CHANNEL))
    return _emit([(head, None, "v"), (_u(key), None, "b"), (_u(count - 1), column, "v"), (_u(length), nl, "v"),
                  (_u(velocity), nv, "b"), (_u(track), nt, "v"), (_u(group), ng, "v"), (_u(channel), nc, "b")])


def event_records(tick, track, msg):
    """Events (tick order, their own order kept on a tick): tick, track arrays + MIDI message bytes each -> body."""
    out, last = [], 0
    for t, k, m in zip(tick.tolist(), track.tolist(), msg):
        out.append(varint(t - last) + varint(k) + m)
        last = t
    return b"".join(out)


def timed(kind, rows, fields):
    """A Tempo / signature block: rows [(tick, ...)] in tick order, fields(row) -> its bytes after the gap."""
    body, last = [], 0
    for r in rows:
        body.append(varint(r[0] - last) + fields(r))
        last = r[0]
    return block(kind, b"".join(body))


def zipped(raw):
    """raw block bytes, inside a Zipped block when that's smaller."""
    z = block(ZIPPED, varint(len(raw)) + zlib.compress(raw, ZIP_LEVEL))
    return z if len(z) < len(raw) else raw


def pack(notes, ppq, track_channels, from_bar=0, length=0, names=None, track_colours=None, channel_colours=None,
         tempo=(), sigs=(), keys=(), events=None):
    """The Spiderweb Notes bytes, or None when no note keeps the rules or they span LONGEST ticks or more.
    notes: int64 rows (start, length, key, velocity, track, group, channel), ticks from the copy's start (tick 0);
    channel 0-15 (OWN_CHANNEL when it's its track's). track_channels: each track's channel, 0.. (Tracks block).
    names {track: text}; track_colours {track: "RRGGBB"}; channel_colours: 16 "RRGGBB"; tempo [(tick, us)], sigs
    [(tick, top, bottom)], keys [(tick, sharps, minor)] from tick 0 (the one in force there first); events:
    (tick, track arrays, [MIDI bytes]) in their order, or None. Packed PIECE records at a time, each piece its
    own block (zipped when smaller), so a big copy never needs much memory at once."""
    a = np.asarray(notes, np.int64).reshape(-1, 7)
    order = kept_order(*a.T)
    if not len(order):
        return None
    span = max(int((a[order, 0] + a[order, 1]).max()), length)
    if events is not None and len(events[0]):
        span = max(span, int(events[0].max()))
    if span >= LONGEST:
        return None
    out = [TAG, bytes([VERSION]), block(INFO, varint(ppq) + varint(len(order)) + varint(from_bar) + varint(length)),
           block(TRACKS, varint(len(track_channels)) + bytes(int(c) & 15 for c in track_channels))]
    if names:
        out.append(block(NAMES, varint(len(names)) + b"".join(varint(t) + text(names[t]) for t in sorted(names))))
    if track_colours:
        out.append(block(TRACK_COLOURS, varint(len(track_colours)) + b"".join(
            varint(t) + bytes.fromhex(track_colours[t].lstrip("#")[:6]) for t in sorted(track_colours))))
    if channel_colours:
        out.append(block(CHANNEL_COLOURS, b"".join(bytes.fromhex(c.lstrip("#")[:6]) for c in channel_colours[:16])))
    if tempo:
        out.append(timed(TEMPO, tempo, lambda r: varint(r[1])))
    if sigs:
        out.append(timed(TIME_SIGS, sigs, lambda r: bytes([r[1], r[2]])))
    if keys:
        out.append(timed(KEY_SIGS, keys, lambda r: bytes([r[1] & 0xFF, r[2]])))
    for at in range(0, len(order), PIECE):
        rows = a[order[at:at + PIECE]]
        out.append(zipped(block(NOTES, records(tuple(rows[:, k] for k in range(7))))))
    if events is not None:
        tick, track, msg = events
        for at in range(0, len(tick), PIECE):
            out.append(zipped(block(EVENTS, event_records(tick[at:at + PIECE], track[at:at + PIECE],
                                                         msg[at:at + PIECE]))))
    out.append(varint(END))
    return b"".join(out)
