"""Writes a standard MIDI file (format 1) by hand."""

import os
import struct
import threading

import numpy as np

from files.midi_bytes import vlq
from files.safefile import write_bytes
from files.own_tracks import among_file, plan
from files.speed import loops
from notes.engine import OWN0, auto_count, own_id, own_slot, slot_port, slot_ports, slot_track_channel

PPQ_WARN = 32767  # a PPQ this high or higher: many MIDI programs can't open the file; still written
MAX_DELTA = (1 << 28) - 1  # the longest wait between two events a MIDI file can hold (4 bytes)
MAX_TRACKS = 65535 - 1  # note tracks a MIDI file can hold (its header counts 16 bits, the tempo track is one)
PIECE = 1 << 20  # events made at a time without the speed-ups
END = b"\x00\xFF\x2F\x00"  # end-of-track
TRACK_WORKERS = 8  # tracks made side by side (with the speed-ups)
AHEAD_NOTES = 16_000_000  # notes in tracks under way / made ahead of the one written at most (~64 bytes each
# while made: the sort's arrays, ~8 bytes once made)
JOB_NOTES = 200_000  # small tracks made together in jobs of about this many notes
SORT_OWN_FROM = 1_000_000  # notes from which the tracks' sort is the own loops' (key_order_cores; below: NumPy's)


def track_events(notes, ch):
    """One track's events (a note-on and a note-off per note) as byte pieces (uint8 arrays), end-of-track not
    included. Events in time order, note-offs first on a shared tick, otherwise in the notes' order. ch: the
    channel, or each note's (a track of yours holding several: a folder in the track list)."""
    n = len(notes)
    tick = np.empty(2 * n, np.int64)  # on, off, on, off, ... as tick * 2, + 1 on a note-on (so offs come first)
    tick[0::2] = notes[:, 0] * 2 + 1
    tick[1::2] = notes[:, 1] * 2
    order = np.argsort(tick, kind="stable")  # (one number sorts in half the time of three)
    fast = loops()
    if fast is not None and np.ndim(ch) == 0:  # (the bytes in one pass: ~3x quicker)
        del tick
        return [fast.midi_events(np.ascontiguousarray(notes), np.arange(n), order, ch, MAX_DELTA)]
    # (in pieces: the NumPy way makes many arrays per event)
    return [_events(notes, order[at:at + PIECE], tick, int(tick[order[at - 1]] >> 1) if at else 0, ch)
            for at in range(0, 2 * n, PIECE)]


def own_events(cc, track_id):
    """A track of yours' (or a loaded MIDI's track drawn into: FILE0 + n) MIDI messages from cc ({slot: [(tick,
    status, data 1, data 2, ...)]}, App.cc_messages: pasted notes' CC events laid there, user 2026-10-29), every
    channel's: (events in time order, each event's channel), or ([], []) with none."""
    got = [(e, c) for c in range(16) for e in cc.get(own_slot(track_id, c)) or () if e[1] is not None]
    got.sort(key=lambda g: g[0][0])  # (stable: a channel's own order kept on a shared tick)
    return [e for e, _ in got], [c for _, c in got]


def note_inserts(notes, events=(), chans=()):
    """Notes put into a loaded MIDI's track (group D: shapes drawn into it), as midi_copy.Retime inserts: (ticks,
    starts of each event in the bytes, bytes), every event with its status byte (channel = slot & 15), in time
    order, note-offs first on a shared tick (like track_events). events, chans: own_events' (the CC events laid
    there), between the note-offs and the note-ons of their tick (like cc_track)."""
    if len(events):
        items = [(int(s) * 3 + 2, bytes([0x90 | int(k) & 15, int(p), int(v)])) for s, _, p, v, k in notes[:, :5]]
        items += [(int(e) * 3, bytes([0x80 | int(k) & 15, int(p), 0])) for _, e, p, _, k in notes[:, :5]]
        for e, c in zip(events, chans):
            short = (e[1] & 0xE0) == 0xC0  # (program change, channel pressure: one data byte)
            items.append((e[0] * 3 + 1, bytes([e[1] | c, e[2]] if short else [e[1] | c, e[2], e[3]])))
        items.sort(key=lambda it: it[0])
        sizes = np.array([len(b) for _, b in items], np.int64)
        return (np.array([k // 3 for k, _ in items], np.int64), np.concatenate([[0], np.cumsum(sizes)]),
                np.frombuffer(b"".join(b for _, b in items), np.uint8))
    n = len(notes)
    tick = np.empty(2 * n, np.int64)
    tick[0::2] = notes[:, 0] * 2 + 1
    tick[1::2] = notes[:, 1] * 2
    order = np.argsort(tick, kind="stable")
    note, on = order >> 1, (order & 1) ^ 1
    data = np.empty((2 * n, 3), np.uint8)
    data[:, 0] = np.where(on == 1, 0x90, 0x80) | (notes[note, 4] & 15)
    data[:, 1] = notes[note, 2]
    data[:, 2] = notes[note, 3] * on
    return tick[order] >> 1, np.arange(2 * n + 1, dtype=np.int64) * 3, data.ravel()


def _events(notes, order, tick, last, ch):
    """track_events' bytes for some events in time order; last = the tick before the first of them."""
    tick, on, note = tick[order] >> 1, (order & 1) ^ 1, order >> 1
    if np.ndim(ch):  # (each note's own channel)
        ch = ch[note]
    key = notes[note, 2]
    vel = notes[note, 3] * on
    return _encode(tick, np.where(on == 1, 0x90 | ch, 0x80 | ch), key, vel, last)


def cc_track(notes, ch, events, ev_ch=None):
    """track_events for a track with other MIDI messages too (a Hz bass's CC lines: hz_cc.slot_events, [(tick,
    status, data 1, data 2)], status without the channel): on a shared tick the note-offs first, then the messages
    (in their order), then the note-ons (a note starts with the values set on its tick). ch: the channel, or each
    note's; ev_ch: each message's channel (a track of yours with several: own_events), else ch."""
    n = len(notes)
    # (pasted notes' passengers: Domino's own events have no MIDI message (status None), left out)
    keep = [e[1] is not None for e in events]
    ev = np.asarray([e[:4] for e, k in zip(events, keep) if k], np.int64).reshape(-1, 4)
    ech = ch if ev_ch is None else np.asarray(ev_ch, np.int64)[np.array(keep, bool)] if len(keep) else 0
    tick = np.concatenate([notes[:, 1], ev[:, 0], notes[:, 0]])
    kind = np.repeat(np.arange(3), [n, len(ev), n])
    order = np.lexsort((kind, tick))  # (stable: alike ones keep their order)
    status = np.concatenate([np.full(n, 0x80) | ch, ev[:, 1] | ech, np.full(n, 0x90) | ch])
    one = np.concatenate([notes[:, 2], ev[:, 2], notes[:, 2]])
    two = np.concatenate([np.zeros(n, np.int64), ev[:, 3], notes[:, 3]])
    status = status[order]
    short = (status & 0xE0) == 0xC0  # (program change, channel pressure: one data byte)
    return [_encode(tick[order], status, one[order], two[order], 0, short if short.any() else None)]


def _encode(tick, status, key, vel, last, short=None):
    """Events (3-byte messages) in time order as bytes, delta times from last (the tick before the first). short:
    True for the events with one data byte (program change, channel pressure: vel left out)."""
    delta = np.diff(tick, prepend=last)
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
        if short is not None:
            short = np.repeat(short, fill + 1) & ~filler
    if short is not None:
        return _encode_short(delta, status, key, vel, short)
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


def _encode_short(delta, status, key, vel, short):
    """_encode's bytes when some events have one data byte (short); delta = the delta times, fillers in."""
    width = np.where(short, 2, 3)
    size = np.ones(len(delta), np.int64)
    for bits in (7, 14, 21):
        size += delta >= 1 << bits
    at = np.cumsum(size + width) - (size + width)
    out = np.zeros(int((size + width).sum()), np.uint8)
    for k in range(int(size.max())):
        has = size > k
        left = size[has] - 1 - k
        out[at[has] + k] = (delta[has] >> (7 * left)) & 0x7F | np.where(left > 0, 0x80, 0)
    out[at + size] = status
    out[at + size + 1] = key
    full = ~short
    out[at[full] + size[full] + 2] = vel[full]
    return out


def empty_track(ch):
    """An empty track's events, so editors still show its channel (a track has no channel of its own, only its
    events do; with none they showed channel 1, user 2026-10-17): a channel prefix (FF 20) and a note-off for a note
    never started (key 0, sounds nothing; Domino reads only this one, measured)."""
    return bytes([0, 0xFF, 0x20, 1, ch, 0, 0x80 | ch, 0, 0])


def long_silences(notes, tick_map=None):
    """True if some track has a silence too long for one step (written with empty events in between). tick_map:
    the notes' ticks -> the file's (Keep timing's plan map: a part drawn faster than heard stretches the waits)."""
    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    if not len(notes):
        return False
    s, e = notes[:, 0], notes[:, 1]
    top = int(e.max()) if tick_map is None else int(tick_map(np.array([e.max()], np.int64))[0])
    if top <= MAX_DELTA:  # (the whole song is shorter than one step)
        return False
    if tick_map is not None:
        s, e = tick_map(s), tick_map(e)
    # (the tracks one after another in one sort, each its own slice: a pass over every note per track took
    # seconds with thousands of tracks; hunt 2026-11-27)
    order = np.argsort(notes[:, 4], kind="stable")
    slots = notes[order, 4]
    cuts = np.concatenate([[0], np.flatnonzero(slots[1:] != slots[:-1]) + 1, [len(slots)]])
    for a, b in zip(cuts[:-1].tolist(), cuts[1:].tolist()):
        rows = order[a:b]
        tick = np.sort(np.concatenate([s[rows], e[rows]]))
        if tick[0] > MAX_DELTA or np.diff(tick).max(initial=0) > MAX_DELTA:
            return True
    return False


def _chunk(data):
    return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)



def track_slots(notes, pad=True, least=0):
    """The automatic slots written as tracks, in order: every slot up to the last one used (pad: empty ones too, so
    each shows its channel), or only the ones with notes ("Fill each round with empty tracks" off). The tracks
    added by hand (engine.OWN0) aren't among them (own_tracks.plan puts them in). least: slots 0.. at least
    (App.least_tracks: a new project's empty Track 1)."""
    if not len(notes):
        return np.arange(least, dtype=np.int64)
    if pad:
        return np.arange(max(auto_count(notes[:, 4]), least), dtype=np.int64)
    slots = np.unique(np.asarray(notes[:, 4], np.int64))
    slots = slots[:np.searchsorted(slots, OWN0)]
    return slots if len(slots) >= least else np.arange(least, dtype=np.int64)


def own_compact(notes, own=(), floor=0, chans=False):
    """notes with the slots of tracks added by hand (engine.OWN0 + ...) numbered just past the automatic ones, in
    own's order (the rest after): (notes, automatic slots, slots in all, {own track id: its slot}); notes unchanged
    (not copied) without any. floor: automatic slots at least (an empty Track 1 written: track_slots' least).
    chans: ONE slot per track of yours even when it holds several channels (a folder, PLAN #11) + a 5th answer =
    each note's channel there (-1 on the automatic ones)."""
    autos = max(auto_count(notes[:, 4]), floor)
    if not len(notes) or notes[:, 4].max() < OWN0:
        return (notes, autos, autos, {}) + ((np.full(len(notes), -1, np.int64),) if chans else ())
    s = notes[:, 4]
    mine = s >= OWN0
    key = (s[mine] - OWN0) >> 4 << 4 if chans else s[mine] - OWN0  # (a track's channels together: channel bits off)
    ids = np.unique(key)
    place = {t["id"]: k for k, t in enumerate(own)}
    ranked = sorted(range(len(ids)), key=lambda k: (place.get(int(ids[k]) >> 4, len(place)), k))
    new = np.empty(len(ids), np.int64)
    new[ranked] = autos + np.arange(len(ids))
    every = np.full(len(notes), -1, np.int64) if chans else None
    if chans:
        every[mine] = s[mine] & 15
    notes = notes.copy()
    notes[mine, 4] = new[np.searchsorted(ids, key)]
    at = {}
    for k, v in enumerate(ids.tolist()):  # (several channels without chans: the first one's, own_parts the rest)
        at.setdefault(int(v) >> 4, int(new[k]))
    return (notes, autos, autos + len(ids), at) + ((every,) if chans else ())


def own_parts(at, count):
    """{own track id: its slots after own_compact (chans off), one per channel it holds} from at: a track's
    channels are numbered side by side from its first (at[id]) up to the next track's (Domino's track = one
    channel: a folder of yours goes out as one track per channel)."""
    firsts = sorted(at.values())
    after = dict(zip(firsts, firsts[1:] + [count]))
    return {i: list(range(s, after[s])) for i, s in at.items()}


def tempo_track(bpm, beats, tempo=None, sigs=None, keys=None):
    """The first track: the tempo (or the Tempo pane's changes, [(tick, microseconds per beat)] from
    tempo_file.file_plan, tick 0 first) and the time signature (or sigs = [(tick, top, bottom power)], tick 0
    first) and key signatures (keys = [(tick, sharps -7..7, minor)], FF 59)."""
    head = bytearray()
    tempo = tempo or [(0, round(60_000_000 / bpm))]
    sigs = sigs or [(0, beats, 2)]
    if len(tempo) + len(sigs) >= TRACK_ARRAYS_FROM:
        return _tempo_track_arrays(tempo, sigs, keys or ())
    events = sorted([(t, 1, bytes([0xFF, 0x58, 4, n, d, 24, 8])) for t, n, d in sigs]
                    + [(t, 0, b"\xFF\x51\x03" + int(us).to_bytes(3, "big")) for t, us in tempo]
                    + [(t, 2, bytes([0xFF, 0x59, 2, sf & 0xFF, int(mi)])) for t, sf, mi in keys or ()])
    last = 0
    for tick, _, data in events:
        delta = tick - last
        while delta > MAX_DELTA:  # (a wait too long for one step: empty text events, as in note tracks)
            head += vlq(MAX_DELTA) + b"\xFF\x01\x00"
            delta -= MAX_DELTA
        head += vlq(delta) + data
        last = tick
    head += vlq(0) + b"\xFF\x2F\x00"
    return head


TRACK_ARRAYS_FROM = 4096  # a first track of this many events or more: made with arrays (the same bytes)
FILLER = bytes([0xFF, 0xFF, 0xFF, 0x7F, 0xFF, 0x01, 0x00])  # vlq(MAX_DELTA) + an empty text event


def _table(rows, w):
    """Events as an (n, w) int64 table: tempo_file.Rows' own, else made from the tuples."""
    a = getattr(rows, "arr", None)
    return np.asarray(a if a is not None else list(rows), np.int64).reshape(-1, w)


TRACK_PIECE = 1 << 20  # events of one kind in one piece of the first track (PLAN export in pieces: the work lists of
# 17 M tempo changes at once took 2.9 GB)


def _tempo_track_arrays(tempo, sigs, keys):
    """tempo_track for many events (PLAN fit bar lines D2: millions of fitted bars), the same bytes made with arrays:
    the events in sorted()'s order (tick, then tempo / time / key signature, then their bytes), waits past MAX_DELTA
    split with empty text events, every wait a variable-length number. Made a stretch of ticks at a time (each
    table's TRACK_PIECE next events at most, all events of a tick together), so the work lists stay small."""
    t, s, k = _table(tempo, 2), _table(sigs, 3), _table(keys, 3)
    tables = (t, s, k)
    if not all(len(x) < 2 or bool((x[1:, 0] >= x[:-1, 0]).all()) for x in tables):
        return bytearray(_track_bytes(t, s, k, 0)[0].tobytes()) + b"\x00\xFF\x2F\x00"  # (out of order: at once)
    ticks = [np.ascontiguousarray(x[:, 0]) for x in tables]
    pos, last, parts = [0, 0, 0], 0, []
    while any(p < len(x) for p, x in zip(pos, tables)):
        ahead = [int(tk[p + TRACK_PIECE]) for p, tk in zip(pos, ticks) if p + TRACK_PIECE < len(tk)]
        lo = min(int(tk[p]) for p, tk in zip(pos, ticks) if p < len(tk))
        upto = max(min(ahead), lo + 1) if ahead else None  # (ticks before this one: never an empty piece)
        ends = [len(tk) if upto is None else int(np.searchsorted(tk, upto, "left")) for tk in ticks]
        got, last = _track_bytes(*(x[p:e] for x, p, e in zip(tables, pos, ends)), last)
        parts.append(got)
        pos = ends
    parts.append(np.array([0x00, 0xFF, 0x2F, 0x00], np.uint8))
    return bytearray().join(parts)


def _track_bytes(t, s, k, last):
    """_tempo_track_arrays' bytes for these events (no end of track), the first wait counted from tick `last`:
    (uint8 array, the last event's tick)."""
    tick = np.concatenate([t[:, 0], s[:, 0], k[:, 0]])
    kind = np.repeat(np.array([0, 1, 2], np.int64), [len(t), len(s), len(k)])
    # (the bytes after each kind's fixed start, as sort keys: tempo's 3 bytes = the number itself)
    k1 = np.concatenate([t[:, 1], s[:, 1], k[:, 1] & 0xFF])
    k2 = np.concatenate([np.zeros(len(t), np.int64), s[:, 2], k[:, 2]])  # (int(mi): a bool is 0 / 1 here too)
    order = np.lexsort((k2, k1, kind, tick))
    tick, kind, k1, k2 = tick[order], kind[order], k1[order], k2[order]
    if not len(tick):
        return np.zeros(0, np.uint8), last
    delta = np.diff(tick, prepend=last)
    fill = np.maximum(0, (delta - 1) // MAX_DELTA)  # (empty text events before it: each waits MAX_DELTA)
    rest = delta - fill * MAX_DELTA
    vl = 1 + (rest >= 1 << 7) + (rest >= 1 << 14) + (rest >= 1 << 21)
    dl = np.array([6, 7, 5], np.int64)[kind]
    size = fill * len(FILLER) + vl + dl
    at = np.concatenate([[0], np.cumsum(size)])
    out = np.empty(int(at[-1]), np.uint8)
    if fill.any():  # (filler j of all = at its event's start + its place among the event's: one index list, moved
        # on a byte at a time in place: a huge-tick song has ~7 a bar)
        nf = int(fill.sum())
        place = np.repeat(at[:-1] - (np.cumsum(fill) - fill) * len(FILLER), fill)
        place += np.arange(0, nf * len(FILLER), len(FILLER), dtype=np.int64)
        for v in FILLER:
            out[place] = v
            place += 1
    p = at[:-1] + fill * len(FILLER)  # (the wait's first byte)
    for n in (1, 2, 3, 4):
        on = vl == n
        if not on.any():
            continue
        q, r = p[on], rest[on]
        for i in range(n):  # (highest 7 bits first, top bit on all but the last)
            out[q + i] = ((r >> (7 * (n - 1 - i))) & 0x7F) | (0x80 if i < n - 1 else 0)
    d = p + vl  # (the event's own bytes)
    for kd, head in ((0, (0xFF, 0x51, 0x03)), (1, (0xFF, 0x58, 0x04)), (2, (0xFF, 0x59, 0x02))):
        on = kind == kd
        if not on.any():
            continue
        q = d[on]
        for i, v in enumerate(head):
            out[q + i] = v
        a, b = k1[on], k2[on]
        if kd == 0:
            for i in range(3):
                out[q + 3 + i] = (a >> (8 * (2 - i))) & 0xFF
        elif kd == 1:
            out[q + 3], out[q + 4], out[q + 5], out[q + 6] = a, b, 24, 8
        else:
            out[q + 3], out[q + 4] = a, b
    return out, int(tick[-1])


def write_midi(path, ppq, bpm, beats, notes, use10=False, pad=True, cc=None, tempo=None, sigs=None, keys=None,
               own=(), least=0, filled=None, tick_map=None):
    """notes: [start, end, pitch, velocity, slot, ...]. Every slot gets its own track with a single channel
    (use10: channel 10 used too, slot_track_channel; pad, least: track_slots). Nothing marks channel 10 as not drums
    (user, 2026-10-12: no messages that could switch something on in their own setup). cc = {slot: messages} for
    the slots' tracks (the Hz basses' CC lines the user drew: hz_cc.slot_events); with them past one round of
    channels every track starts with its port (FF 21: engine.slot_ports). tempo, sigs: tempo_track's. own: the
    tracks added by hand (files/own_tracks.py), put among the slots' (own_tracks.plan; filled: the slots counted as
    having notes, App.filled_slots; None = the notes' own). tick_map: the notes' ticks -> the file's (tempo_file's
    plan "map", Keep timing), applied a track at a time while written (PLAN export in pieces: no moved copy of
    every note); the same file as notes moved first."""
    head = tempo_track(bpm, beats, tempo, sigs, keys)

    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    tracks = plan(track_slots(notes, pad, least), own, np.unique(notes[:, 4]) if filled is None else filled)
    start = (b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks) + 1, ppq) + b"MTrk" + struct.pack(">I", len(head)),
             head)  # (the first track's bytes as they are: one big copy fewer)
    write_bytes(path, _pieces(notes, tracks, use10, start, cc or {}, tick_map=tick_map))


def note_tracks(notes, use10=False, pad=True, cc=None, ports=None, own=(), files=0, filled=None, least=0):
    """write_midi's note tracks alone, no header or tempo track: (pieces, how many tracks, {k: [whole MTrk chunks]}).
    A loaded MIDI's export puts pieces after the file's own tracks, and the last answer's chunks before its k-th
    track (own tracks placed among its files tracks: own_tracks "file"). ports: a set of slots whose tracks start
    with their port (extracted notes from another port: their slots' rounds are their ports). filled / least:
    write_midi's."""
    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    head, rest = among_file(list(own), files)
    tracks = plan(track_slots(notes, pad, least), rest, np.unique(notes[:, 4]) if filled is None else filled)
    before = {}
    if head:
        for t, data in zip(head, track_chunks(notes, head, use10, cc, ports)):
            before.setdefault(t["file"], []).append(data)
    return _pieces(notes, tracks, use10, b"", cc or {}, ports), len(tracks) + len(head), before


def track_chunks(notes, tracks, use10, cc, ports):
    """Each of tracks' whole MTrk chunk (bytes), in order."""
    out, part = [], []
    for piece in _pieces(notes, tracks, use10, b"", cc or {}, ports, ends=True):
        if piece is None:
            out.append(b"".join(part))
            part = []
        else:
            part.append(piece)
    return out


def name_event(name):
    """A track name (FF 03) at tick 0, in Shift-JIS (CP932) like Domino (letters it can't hold: "?")."""
    data = name.encode("cp932", "replace")
    return b"\x00\xFF\x03" + vlq(len(data)) + data


def _pieces(notes, tracks, use10, start, cc, ports=None, ends=False, tick_map=None):
    """write_midi's file as pieces, one track at a time (a big file is never whole in memory). With the speed-ups
    the tracks are made side by side on several cores (midi_track), written in order. tracks: slots, and own
    tracks (dicts: their name first, then the notes drawn into them, on their channel; port A). ends: None
    after each track too (track_chunks). start: bytes, or a tuple of byte pieces. tick_map: write_midi's (each
    track's notes moved to the file's ticks as it's made)."""
    yield from start if isinstance(start, tuple) else (start,)
    # (an empty Track 1 among them, track_slots' least: the own tracks' notes numbered past it)
    notes, autos, count, at, chans = own_compact(notes, floor=max((t + 1 for t in tracks if type(t) is not dict),
                                                                  default=0), chans=True)
    fast = loops()
    if count > 1:  # (one sort finds every track's notes, in their own order)
        # (the own counting pass: the same order, 1.3-1.8x; our own library only: Numba's copy sets aside three
        # note-long work lists even on sorted notes, ~0.7 GB more at 32 M than NumPy's sort)
        if fast is not None and getattr(fast, "x", False) is not False and len(notes) >= SORT_OWN_FROM:
            slots = np.ascontiguousarray(notes[:, 4])
            order = fast.key_order_cores(slots, slots, slots, slots)
        else:
            slots = notes[:, 4].astype(np.int16 if count < 2 ** 15 else np.int64)
            order = np.argsort(slots, kind="stable")
        cuts = np.zeros(count + 1, np.int64)
        np.cumsum(np.bincount(slots, minlength=count), out=cuts[1:])
        del slots
    every, ports = slot_ports(autos, cc, use10), ports or ()
    # (a track of yours given a port in Track Properties: every track says its port, A too, so no player carries
    # one over from the track before; without one the file stays as it always was)
    ported = any(type(t) is dict and t.get("port") for t in tracks)
    def lowest():
        low = notes[:, 0].min()
        return low if tick_map is None else int(tick_map(np.array([low], np.int64))[0])

    if fast is not None and (not len(notes) or lowest() < 0):  # (the radix sort: ticks from 0 only)
        fast = None
    if fast is not None:
        notes = np.ascontiguousarray(notes)
        bits = int(notes[:, 1].max() * 2 + 2).bit_length()

    def take(rows):
        """These notes (a copy) at the file's ticks: tick_map's, else as they are."""
        got = notes[rows]
        if tick_map is not None and len(got):
            got[:, 0], got[:, 1] = tick_map(got[:, 0]), tick_map(got[:, 1])
        return got

    def rows_of(track):
        """The rows of notes going on this track (None: an own track with none drawn into it)."""
        if type(track) is dict:
            track = at.get(track["id"])
            if track is None:
                return None
        return np.arange(len(notes)) if count == 1 else order[cuts[track]:cuts[track + 1]]

    def group_made(g):
        """make() for a job's tracks, their notes moved to the file's ticks ONCE for the whole job (a track at a
        time paid the map's own cost per track: 20,000 small tracks took 3x as long; hunt 2026-11-27)."""
        rs = [rows_of(t) for t in g]
        have = [r for r in rs if r is not None and len(r)]
        moved = take(np.concatenate(have)) if have else notes[:0]
        # (one row list + one radix width for the whole job: made per track they cost tiny tracks ~25 %)
        idx = np.arange(len(moved))
        wide = int(moved[:, 1].max() * 2 + 2).bit_length() if len(moved) else 1
        out, at_ = [], 0
        for t, r in zip(g, rs):
            n = 0 if r is None else len(r)
            out.append(make(t, (moved, idx[at_:at_ + n], at_, wide)))
            at_ += n
        return out

    def make(track, pre=None):
        """The track's event bytes (pieces). pre: (its job's notes moved to the file's ticks, this track's rows
        there, their first place, the job's radix width) from group_made."""
        if pre is not None:
            take_ = lambda rows: pre[0][pre[2]:pre[2] + len(pre[1])]  # noqa: E731 (this track's own, moved)
        else:
            take_ = take
        head, mine_cc = [], cc.get(track) if type(track) is not dict else None
        if type(track) is dict:  # (an own track: its port, its name, its notes if any were drawn into it)
            head = ([bytes([0, 0xFF, 0x21, 1, track.get("port", 0)])] if every or ported else []) + (
                [name_event(track["name"])] if track["name"] else [])
            evs, ev_ch = own_events(cc, track["id"])  # (pasted notes' CC events laid there, every channel's)
            channel, track = track["ch"], at.get(track["id"])
            if track is None:
                return head + [empty_track(channel)]
            rows = np.arange(len(notes)) if count == 1 else order[cuts[track]:cuts[track + 1]]
            mine = chans[rows]
            if evs:
                return head + cc_track(take_(rows), mine if mine.min() != mine.max() else int(mine[0]), evs, ev_ch)
            if mine.min() != mine.max():  # (several channels: a folder, each note on its own)
                return head + track_events(take_(rows), mine)
            channel = int(mine[0])  # (its colours' own channel: moved in from another track)
        else:
            channel = slot_track_channel(track, use10)[1]
            if every or track in ports:
                head = [bytes([0, 0xFF, 0x21, 1, slot_port(track, use10)])]
            elif ported:
                head = [bytes([0, 0xFF, 0x21, 1, 0])]
        if fast is not None and not mine_cc:
            rows = np.arange(len(notes)) if count == 1 else order[cuts[track]:cuts[track + 1]]
            if not len(rows):
                events = [empty_track(channel)]
            elif tick_map is None:
                events = [fast.midi_track(notes, rows, channel, MAX_DELTA, bits)]
            elif pre is not None:  # (moved with its job: its rows in the job's notes)
                events = [fast.midi_track(pre[0], pre[1], channel, MAX_DELTA, pre[3])]
            else:  # (this track's notes moved to the file's ticks: a copy of its own; the radix sort's width by them)
                mine = take_(rows)
                events = [fast.midi_track(mine, np.arange(len(mine)), channel, MAX_DELTA,
                                          int(mine[:, 1].max() * 2 + 2).bit_length())]
        elif tick_map is not None:
            mine = take_(np.arange(len(notes)) if count == 1 else order[cuts[track]:cuts[track + 1]])
            events = (cc_track(mine, channel, mine_cc) if mine_cc else track_events(mine, channel)
                      if len(mine) else [empty_track(channel)])
        else:
            mine = notes if count == 1 else notes[order[cuts[track]:cuts[track + 1]]]
            events = (cc_track(mine, channel, mine_cc) if mine_cc else track_events(mine, channel)
                      if len(mine) else [empty_track(channel)])
        return head + events

    def size(t):
        if type(t) is dict:
            t = at.get(t["id"])
            if t is None:
                return 0
        return int(cuts[t + 1] - cuts[t]) if count > 1 else len(notes)

    # jobs = tracks one after another up to JOB_NOTES notes (tens of thousands of tiny tracks: one hand-over each
    # cost more than making them)
    sizes = [size(t) for t in tracks]
    jobs, weights, group, held = [], [], [], 0
    for t, size in zip(tracks, sizes):
        group.append(t)
        held += size
        if held >= JOB_NOTES:
            jobs.append(group)
            weights.append(held)
            group, held = [], 0
    if group:
        jobs.append(group)
        weights.append(held)
    jobs = [(lambda g=g: [make(t) for t in g]) if tick_map is None else (lambda g=g: group_made(g)) for g in jobs]
    for made in (_in_order(jobs, min(TRACK_WORKERS, os.cpu_count() or 1), weights)
                 if fast is not None and len(jobs) > 1 else (job() for job in jobs)):
        for events in made:
            yield b"MTrk" + struct.pack(">I", sum(len(e) for e in events) + len(END))
            yield from events
            yield END
            if ends:
                yield None
        made = events = None


def _in_order(jobs, n, weights):
    """Each job()'s result, in the jobs' order, made on n DAEMON threads (closing Spiderweb never waits for them).
    Memory: jobs done or under way past the one written weigh (weights) AHEAD_NOTES at most (one at least). A job's
    error is raised here, in its place."""
    cv, results, stop = threading.Condition(), {}, [False]
    nxt = [0]  # (the next job to start)
    want = [0]  # (the next result to hand out)
    held = [0]  # (weight of the jobs started and not handed out)

    def work():
        while True:
            with cv:
                while (not stop[0] and nxt[0] < len(jobs) and nxt[0] > want[0]
                       and held[0] + weights[nxt[0]] > AHEAD_NOTES):
                    cv.wait()
                if stop[0] or nxt[0] >= len(jobs):
                    return
                i = nxt[0]
                nxt[0] += 1
                held[0] += weights[i]
            try:
                got = (True, jobs[i]())
            except BaseException as e:
                got = (False, e)
            with cv:
                results[i] = got
                cv.notify_all()

    threads = [threading.Thread(target=work, daemon=True, name="midi tracks") for _ in range(n)]
    for th in threads:
        th.start()
    try:
        for i in range(len(jobs)):
            with cv:
                while i not in results:
                    cv.wait()
                ok, got = results.pop(i)
                want[0] = i + 1
                held[0] -= weights[i]
                cv.notify_all()
            if not ok:
                raise got
            yield got
            got = None
    finally:
        with cv:
            stop[0] = True
            cv.notify_all()
