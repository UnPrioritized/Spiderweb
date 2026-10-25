"""Writes a standard MIDI file (format 1) by hand."""

import os
import struct
import threading

import numpy as np

from files.safefile import write_bytes
from files.own_tracks import plan
from files.speed import loops
from notes.engine import slot_port, slot_ports, slot_track_channel

PPQ_WARN = 32767  # a PPQ this high or higher: many MIDI programs can't open the file; still written
MAX_DELTA = (1 << 28) - 1  # the longest wait between two events a MIDI file can hold (4 bytes)
MAX_TRACKS = 65535 - 1  # note tracks a MIDI file can hold (its header counts 16 bits, the tempo track is one)
PIECE = 1 << 20  # events made at a time without the speed-ups
END = b"\x00\xFF\x2F\x00"  # end-of-track
TRACK_WORKERS = 8  # tracks made side by side (with the speed-ups)
AHEAD_NOTES = 16_000_000  # notes in tracks under way / made ahead of the one written at most (~64 bytes each
# while made: the sort's arrays, ~8 bytes once made)
JOB_NOTES = 200_000  # small tracks made together in jobs of about this many notes


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
        return [fast.midi_events(np.ascontiguousarray(notes), np.arange(n), order, ch, MAX_DELTA)]
    # (in pieces: the NumPy way makes many arrays per event)
    return [_events(notes, order[at:at + PIECE], tick, int(tick[order[at - 1]] >> 1) if at else 0, ch)
            for at in range(0, 2 * n, PIECE)]


def _events(notes, order, tick, last, ch):
    """track_events' bytes for some events in time order; last = the tick before the first of them."""
    tick, on, note = tick[order] >> 1, (order & 1) ^ 1, order >> 1
    key = notes[note, 2]
    vel = notes[note, 3] * on
    return _encode(tick, np.where(on == 1, 0x90 | ch, 0x80 | ch), key, vel, last)


def cc_track(notes, ch, events):
    """track_events for a track with other MIDI messages too (a Hz bass's CC lines: hz_cc.slot_events, [(tick,
    status, data 1, data 2)], status without the channel): on a shared tick the note-offs first, then the messages
    (in their order), then the note-ons (a note starts with the values set on its tick)."""
    n = len(notes)
    # (pasted notes' passengers: Domino's own events have no MIDI message (status None), left out)
    ev = np.asarray([e[:4] for e in events if e[1] is not None], np.int64).reshape(-1, 4)
    tick = np.concatenate([notes[:, 1], ev[:, 0], notes[:, 0]])
    kind = np.repeat(np.arange(3), [n, len(ev), n])
    order = np.lexsort((kind, tick))  # (stable: alike ones keep their order)
    status = np.concatenate([np.full(n, 0x80 | ch), ev[:, 1] | ch, np.full(n, 0x90 | ch)])
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



def track_slots(notes, pad=True):
    """The slots written as tracks, in order: every slot up to the last one used (pad: empty ones too, so each
    shows its channel), or only the ones with notes ("Fill each round with empty tracks" off)."""
    if not len(notes):
        return np.zeros(0, np.int64)
    return np.arange(int(notes[:, 4].max()) + 1) if pad else np.unique(np.asarray(notes[:, 4], np.int64))


def tempo_track(bpm, beats, tempo=None, sigs=None, keys=None):
    """The first track: the tempo (or the Tempo pane's changes, [(tick, microseconds per beat)] from
    tempo_file.file_plan, tick 0 first) and the time signature (or sigs = [(tick, top, bottom power)], tick 0
    first) and key signatures (keys = [(tick, sharps -7..7, minor)], FF 59)."""
    head = bytearray()
    tempo = tempo or [(0, round(60_000_000 / bpm))]
    sigs = sigs or [(0, beats, 2)]
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


def write_midi(path, ppq, bpm, beats, notes, use10=False, pad=True, cc=None, tempo=None, sigs=None, keys=None,
               own=()):
    """notes: [start, end, pitch, velocity, slot, ...]. Every slot gets its own track with a single channel
    (use10: channel 10 used too, slot_track_channel; pad: track_slots). Nothing marks channel 10 as not drums
    (user, 2026-10-12: no messages that could switch something on in their own setup). cc = {slot: messages} for
    the slots' tracks (the Hz basses' CC lines the user drew: hz_cc.slot_events); with them past one round of
    channels every track starts with its port (FF 21: engine.slot_ports). tempo, sigs: tempo_track's. own: the
    tracks added by hand (files/own_tracks.py), put among the slots' (own_tracks.plan)."""
    head = tempo_track(bpm, beats, tempo, sigs, keys)

    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    count = int(notes[:, 4].max()) + 1 if len(notes) else 0
    tracks = plan(track_slots(notes, pad), own)
    write_bytes(path, _pieces(notes, count, tracks, use10,
                              b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks) + 1, ppq) + _chunk(head), cc or {}))


def note_tracks(notes, use10=False, pad=True, cc=None, ports=None, own=()):
    """write_midi's note tracks alone, no header or tempo track: (pieces, how many tracks). A loaded MIDI's export
    puts them after the file's own tracks. ports: a set of slots whose tracks start with their port (extracted
    notes from another port: their slots' rounds are their ports)."""
    notes = np.asarray(notes, np.int64).reshape(-1, 6)
    count = int(notes[:, 4].max()) + 1 if len(notes) else 0
    tracks = plan(track_slots(notes, pad), own)
    return _pieces(notes, count, tracks, use10, b"", cc or {}, ports), len(tracks)


def name_event(name):
    """A track name (FF 03) at tick 0, in Shift-JIS (CP932) like Domino (letters it can't hold: "?")."""
    data = name.encode("cp932", "replace")
    return b"\x00\xFF\x03" + vlq(len(data)) + data


def _pieces(notes, count, tracks, use10, start, cc, ports=None):
    """write_midi's file as pieces, one track at a time (a big file is never whole in memory). With the speed-ups
    the tracks are made side by side on several cores (midi_track), written in order. tracks: slots, and own
    tracks (dicts: an empty track with their name and channel)."""
    yield start
    if count > 1:  # (one sort finds every track's notes, in their own order)
        slots = notes[:, 4].astype(np.int16 if count < 2 ** 15 else np.int64)
        order = np.argsort(slots, kind="stable")
        cuts = np.zeros(count + 1, np.int64)
        np.cumsum(np.bincount(slots, minlength=count), out=cuts[1:])
        del slots
    every, ports = slot_ports(count, cc, use10), ports or ()
    fast = loops()
    if fast is not None and (not len(notes) or notes[:, 0].min() < 0):  # (the radix sort: ticks from 0 only)
        fast = None
    if fast is not None:
        notes = np.ascontiguousarray(notes)
        bits = int(notes[:, 1].max() * 2 + 2).bit_length()

    def make(track):
        if type(track) is dict:  # (an own track: empty for now, its name first)
            events = ([name_event(track["name"])] if track["name"] else []) + [empty_track(track["ch"])]
            return ([bytes([0, 0xFF, 0x21, 1, 0])] if every else []) + events
        channel = slot_track_channel(track, use10)[1]
        if fast is not None and not cc.get(track):
            rows = np.arange(len(notes)) if count == 1 else order[cuts[track]:cuts[track + 1]]
            events = [fast.midi_track(notes, rows, channel, MAX_DELTA, bits)] if len(rows) else [empty_track(channel)]
        else:
            mine = notes if count == 1 else notes[order[cuts[track]:cuts[track + 1]]]
            events = (cc_track(mine, channel, cc[track]) if cc.get(track) else track_events(mine, channel)
                      if len(mine) else [empty_track(channel)])
        if every or track in ports:
            events = [bytes([0, 0xFF, 0x21, 1, slot_port(track, use10)])] + events
        return events

    # jobs = tracks one after another up to JOB_NOTES notes (tens of thousands of tiny tracks: one hand-over each
    # cost more than making them)
    sizes = [0 if type(t) is dict else int(cuts[t + 1] - cuts[t]) if count > 1 else len(notes) for t in tracks]
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
    jobs = [lambda g=g: [make(t) for t in g] for g in jobs]
    for made in (_in_order(jobs, min(TRACK_WORKERS, os.cpu_count() or 1), weights)
                 if fast is not None and len(jobs) > 1 else (job() for job in jobs)):
        for events in made:
            yield b"MTrk" + struct.pack(">I", sum(len(e) for e in events) + len(END))
            yield from events
            yield END
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
