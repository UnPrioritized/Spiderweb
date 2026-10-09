"""Writes a standard MIDI file (format 1) by hand."""

import struct

import numpy as np

from files.safefile import write_bytes
from files.speed import loops
from notes.engine import slot_track_channel

PPQ_WARN = 32767  # a PPQ this high or higher: many MIDI programs can't open the file; still written
MAX_DELTA = (1 << 28) - 1  # the longest wait between two events a MIDI file can hold (4 bytes)
PIECE = 1 << 20  # events made at a time without the speed-ups
END = b"\x00\xFF\x2F\x00"  # end-of-track


def vlq(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def track_events(notes, ch):
    """One track's events (a note-on and a note-off per note) as byte pieces (uint8 arrays), end-of-track not
    included. Events in time order, note-offs first on a shared tick, otherwise in the notes' order."""
    n = len(notes)
    tick = np.empty(2 * n, np.int64)  # on, off, on, off, ... as tick * 2, + 1 on a note-on (so offs come first)
    tick[0::2] = notes[:, 0] * 2 + 1
    tick[1::2] = notes[:, 1] * 2
    order = np.argsort(tick, kind="stable")  # (one number sorts in half the time of three)
    fast = loops()
    if fast is not None:  # (the bytes in one pass: ~3x quicker)
        del tick
        return [fast.midi_events(np.ascontiguousarray(notes), order, ch, MAX_DELTA)]
    # (in pieces: the NumPy way makes many arrays per event)
    return [_events(notes, order[at:at + PIECE], tick, int(tick[order[at - 1]] >> 1) if at else 0, ch)
            for at in range(0, 2 * n, PIECE)]


def _events(notes, order, tick, last, ch):
    """track_events' bytes for some events in time order; last = the tick before the first of them."""
    tick, on, note = tick[order] >> 1, (order & 1) ^ 1, order >> 1
    key = notes[note, 2]
    vel = notes[note, 3] * on
    delta = np.diff(tick, prepend=last)
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
    for k in range(int(size.max())):
        has = size > k
        left = size[has] - 1 - k  # 7-bit groups still to come after this byte
        out[at[has] + k] = (delta[has] >> (7 * left)) & 0x7F | np.where(left > 0, 0x80, 0)
    out[at + size] = status
    out[at + size + 1] = key
    out[at + size + 2] = vel
    return out


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


def write_midi(path, ppq, bpm, beats, notes, use10=False):
    """notes: [start, end, pitch, velocity, slot, ...]. Every slot gets its own track with a single channel
    (use10: pictures use channel 10 too, slot_track_channel)."""
    head = bytearray()
    head += vlq(0) + b"\xFF\x51\x03" + round(60_000_000 / bpm).to_bytes(3, "big")
    head += vlq(0) + bytes([0xFF, 0x58, 4, beats, 2, 24, 8])
    head += vlq(0) + b"\xFF\x2F\x00"

    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    count = int(notes[:, 4].max()) + 1 if len(notes) else 0
    write_bytes(path, _pieces(notes, count, use10, b"MThd" + struct.pack(">IHHH", 6, 1, count + 1, ppq) + _chunk(head)))


def _pieces(notes, count, use10, start):
    """write_midi's file as pieces, one track at a time (a big file is never whole in memory)."""
    yield start
    if count > 1:  # (one sort finds every track's notes, in their own order)
        slots = notes[:, 4].astype(np.int16 if count < 2 ** 15 else np.int64)
        order = np.argsort(slots, kind="stable")
        cuts = np.searchsorted(slots[order], np.arange(count + 1))
        del slots
    for track in range(count):
        events = track_events(notes if count == 1 else notes[order[cuts[track]:cuts[track + 1]]],
                              slot_track_channel(track, use10)[1])
        yield b"MTrk" + struct.pack(">I", sum(len(e) for e in events) + len(END))
        yield from events
        events = None
        yield END
