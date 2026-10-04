"""Writes a standard MIDI file (format 1) by hand."""

import struct

import numpy as np

from files.safefile import write_bytes
from notes.engine import slot_track_channel

PPQ_WARN = 32767  # a PPQ this high or higher: many MIDI programs can't open the file; still written
MAX_DELTA = (1 << 28) - 1  # the longest wait between two events a MIDI file can hold (4 bytes)


def vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def track_data(notes, ch):
    """One track's events (a note-on and a note-off per note) as bytes, ending with end-of-track. Events in time
    order, note-offs first on a shared tick, otherwise in the notes' order."""
    n = len(notes)
    tick = np.column_stack([notes[:, 0], notes[:, 1]]).ravel()  # on, off, on, off, ...
    on = np.tile(np.array([1, 0], np.int64), n)
    order = np.lexsort((np.arange(2 * n), on, tick))
    tick, on = tick[order], on[order]
    key = np.repeat(notes[:, 2], 2)[order]
    vel = np.repeat(notes[:, 3], 2)[order] * on
    delta = np.diff(tick, prepend=0)
    status = np.where(on == 1, 0x90 | ch, 0x80 | ch)
    if len(delta) and delta.max() > MAX_DELTA:
        # a silence too long for one step: empty text events (FF 01 00, 3 bytes like a note's) every MAX_DELTA
        fill = np.maximum(delta - 1, 0) // MAX_DELTA  # (fillers before each event)
        last = np.cumsum(fill + 1) - 1  # where each real event lands
        delta, status, key, vel = (np.repeat(a, fill + 1) for a in (delta, status, key, vel))
        filler = np.ones(len(delta), bool)
        filler[last] = False
        delta[filler] = MAX_DELTA
        delta[last] -= fill * MAX_DELTA
        status[filler], key[filler], vel[filler] = 0xFF, 0x01, 0x00
    # variable-length delta times: 7 bits per byte, every byte but the last with the top bit set
    size = np.ones(len(delta), np.int64)
    for bits in (7, 14, 21):
        size += delta >= 1 << bits
    at = np.cumsum(size + 3) - (size + 3)  # where each event starts
    out = np.zeros(int((size + 3).sum()), np.uint8)
    for k in range(int(size.max()) if n else 0):
        has = size > k
        left = size[has] - 1 - k  # 7-bit groups still to come after this byte
        out[at[has] + k] = (delta[has] >> (7 * left)) & 0x7F | np.where(left > 0, 0x80, 0)
    out[at + size] = status
    out[at + size + 1] = key
    out[at + size + 2] = vel
    return out.tobytes() + vlq(0) + b"\xFF\x2F\x00"


def long_silences(notes):
    """True if some track has a silence too long for one step (written with empty events in between)."""
    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    if not len(notes) or notes[:, 1].max() <= MAX_DELTA:  # (the whole song is shorter than one step)
        return False
    for slot in np.unique(notes[:, 4]):
        own = notes[notes[:, 4] == slot]
        tick = np.sort(np.concatenate([own[:, 0], own[:, 1]]))
        if tick[0] > MAX_DELTA or np.diff(tick).max(initial=0) > MAX_DELTA:
            return True
    return False


def _chunk(data):
    return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)


def write_midi(path, ppq, bpm, beats, notes):
    """notes: [start, end, pitch, velocity, slot, ...]. Every slot gets its own track with a single channel."""
    head = bytearray()
    head += vlq(0) + b"\xFF\x51\x03" + round(60_000_000 / bpm).to_bytes(3, "big")
    head += vlq(0) + bytes([0xFF, 0x58, 4, beats, 2, 24, 8])
    head += vlq(0) + b"\xFF\x2F\x00"

    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    chunks = [_chunk(head)]
    for track in range(int(notes[:, 4].max()) + 1 if len(notes) else 0):
        chunks.append(_chunk(track_data(notes[notes[:, 4] == track], slot_track_channel(track)[1])))

    write_bytes(path, b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), ppq) + b"".join(chunks))
