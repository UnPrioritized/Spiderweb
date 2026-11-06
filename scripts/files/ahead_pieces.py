"""Rendered ahead's pieces (PLAN.md R2): the song's sound made into audio ahead of the play line, in the background.

The song (Song: your shapes' notes, their CC lines, the tempo as heard) is cut into BLOCK_S-second blocks on the
song's own clock (block i = frames i x BLOCK .. (i + 1) x BLOCK). A PIECE = a run of blocks made in one go: the first
one from where the making starts is FIRST_RUN blocks (a quick start), later ones up to RUN blocks (Merlin's cost is
mostly fixed per piece: 2 s 0.26 s, 10 s 0.35 s). Each block is kept by itself: the grey, edits (made again) and the
player all work per block, as in the Hz preview (window/hz_preview.py).

A piece starts PRE seconds early and that part is dropped (notes already ringing sound right, the voice limit is
already full). A note still held at that point moves the start back to its own start (at most HELD_PRE seconds:
a note held longer starts there, struck again out of hearing). Engines:
- Merlin / Kestrel (files/kestrel.py Piece): the piece written as a small MIDI file (format 1: tempo track + one
  track per port, FF 21), made on the graphics card with the soundfonts already there (kestrel.loaded_for).
  Channel 10 of each port is melodic (GS "use for rhythm part" off), as your shapes' are in live play.
- BASSMIDI (files/synth.py Synth.render): straight from the song's event list, started as early (pre_frames).
Each piece is made with the limiter OFF: Spiderweb's own runs over the joined pieces (R3).

Thread rules: Maker.step() runs on the window's thread (quick: it only looks at jobs and starts threads); the
making runs in threads of its own; Maker.take() is called from the sound's thread."""

import bisect
import math
import os
import tempfile
import threading
import time

import numpy as np

from files import errors
from files.playback import TimeMap
from files.synth import RATE

class PieceMidi(bytearray):
    """A piece's MIDI file bytes + clock_seed: what Kestrel's clock starts with carried (Song.clock_at)."""

    def __init__(self, data=b"", clock_seed=0.0):
        super().__init__(data)
        self.clock_seed = clock_seed


BLOCK_S = 2.0  # seconds a block (the grey / remake / play unit)
BLOCK = int(BLOCK_S * RATE)
FIRST_RUN = 1  # blocks in the first piece of a run of missing blocks next to the play line (a quick start)
RUN = 5  # blocks in a later piece (10 s)
PRE = 1.0  # seconds a piece starts early (dropped)
POST_S = 0.3  # ... and its events go this far past its end: Merlin looks ahead (vs its render of the whole file,
# Cats Dance 2026-11-03: 0.1 s = -43 dB in the last 10 ms, 0.3 s = -126 dB, 1 s = the same to the bit)
HELD_PRE = 30.0  # ... at most, for a note still held there (8 s: an 18 s held note was -14 dB off at 12 s)
TAIL = 30.0  # seconds of ring after the last note at most (a piece finding the sound died away ends it sooner)
BEHIND = 10.0  # seconds of made blocks kept behind the play line (further back = thrown away when over KEPT_MOST_S)
KEPT_MOST_S = 600.0  # made sound kept outside what's wanted (M2, user 2026-11-03: the play line moved back plays at
# once), farthest from the play line thrown away first; 600 s = 230 MB
PIECE_MOST = 512 << 20  # a loaded MIDI's piece WRITES about this many bytes at most (more blocks = shorter piece; a
# block over it = made in parts) ...
PIECE_LEAST = 32 << 20  # ... or 1/16 of the free memory, not below this (Merlin holds ~5x the piece)
COUNTED_MOST = 4 << 30  # ... a piece for our Merlin with its notes counted (PLAN D2): the same bytes estimate, up to
# this or 1/2 of the free memory (Shinki's 457 s, 1 s in one piece: ~3.5 GB of notes walked, +1.4 GB at most; cut
# small, each part walked every track again: 32 parts = ~290 s, one = 27 s)
BYTES_A_SECOND = RATE * 2 * 4  # (made sound: stereo float32)
MEMORY_SHARE = 0.25  # the made sound never takes more than this share of the free memory (less is made ahead)
FREE_EVERY = 2.0  # seconds between asking the system for free memory
GPU_WORKERS = 1  # Merlin / Kestrel pieces made at once (one graphics card)
OVERLAP_WORKERS = 2  # ... a loaded MIDI's counted Merlin pieces: the next one's MIDI made while Merlin plays the one
# before (CARD_LOCK keeps the card to one; PLAN E3: Shinki's 432 s, 10 s pieces = MIDI 10 s + Merlin 9 s in a row)
CPU_WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))  # BASSMIDI pieces made at once (one core each)
BUSY_MOST = 2  # a loaded MIDI's BASSMIDI pieces being made at once copy at most this many piece caps together
# (BASSMIDI holds ~2x a piece's bytes, measured on a dense wall: a full 512 MB cap piece = +1 GB)
# GS "use for rhythm part" off for part 10 (Kestrel + Merlin read it: midi.rs DrumPart)
_MELODIC_10 = bytes([0xF0, 0x0A, 0x41, 0x10, 0x42, 0x12, 0x40, 0x10, 0x15, 0x00, 0x1B, 0xF7])


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _track(events, tail=b""):
    """[(tick, bytes)] (in order) + tail (already encoded, after them) as an MTrk chunk."""
    body, last = bytearray(), 0
    for t, data in events:
        body += _vlq(t - last) + data
        last = t
    body += tail + b"\x00\xff\x2f\x00"
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def _vlq_many(d):
    """Many delta times at once: (bytes each, the bytes in a row)."""
    d = d.astype(np.int64)
    n = np.ones(len(d), np.int64)
    for k in (7, 14, 21):
        n += d >= (1 << k)
    out = np.zeros(int(n.sum()), np.uint8)
    end = np.cumsum(n)
    for k in range(4):
        sel = n > k
        out[end[sel] - 1 - k] = ((d[sel] >> (7 * k)) & 127) | (0x80 if k else 0)
    return n, out


def _messages(ticks, st, d1, d2, last=0):
    """Short messages (ticks in order, from tick `last`; program / channel pressure = 2 bytes, the rest 3) as one
    encoded run of track bytes."""
    if not len(ticks):
        return b""
    nb, vb = _vlq_many(np.diff(np.concatenate([[last], ticks])))
    three = ~(((st & 0xF0) == 0xC0) | ((st & 0xF0) == 0xD0))
    size = nb + 2 + three
    start = np.cumsum(size) - size
    body = np.zeros(int(size.sum()), np.uint8)
    vstart = np.cumsum(nb) - nb
    body[np.repeat(start, nb) + (np.arange(int(nb.sum())) - np.repeat(vstart, nb))] = vb
    at = start + nb
    body[at], body[at + 1] = st, d1
    body[(at + 2)[three]] = d2[three]
    return body.tobytes()


class Song:
    """What the pieces are made of. notes = App.play_parts' rows (start, end, key, velocity, slot ... in ticks),
    ccs = its [(tick, message)] (playback.cc_list: channel | port << 24), ports = its synth ports (0 = one port),
    tempo = heard [(tick, us)] from tick 0 (None = one tempo: bpm). Keys over 127 are left out, unless edo_first:
    31-EDO's sound (R6, like live play: playback.edo_code), each row on its own pitch-bent channel from that channel
    (port x 16 + channel), the bends at every piece's start."""

    def __init__(self, notes, ccs, ports, use10, ppq, bpm, tempo=None, edo_first=None):
        from notes.engine import channels_of, ports_of
        notes = np.asarray(notes)
        self.edo_first = edo_first
        if len(notes) and edo_first is None:
            notes = notes[notes[:, 2] <= 127]
        self.ppq = ppq
        self.tempo = list(tempo) if tempo else [(0, int(round(60e6 / bpm)))]
        self.map = TimeMap(ppq, self.tempo)
        # MEMORY (user 2026-11-03: keep it in check): only what pieces need, small types (~20 bytes a note;
        # 1 M notes = 20 MB), in start order; App's own note table is not kept
        n = len(notes)
        order = np.argsort(notes[:, 0], kind="stable") if n else np.zeros(0, np.int64)
        col = (lambda c, t: notes[order, c].astype(t)) if n else (lambda c, t: np.zeros(0, t))
        self.start, self.end = col(0, np.int64), col(1, np.int64)
        self.key = col(2, np.uint8)
        self.vel = np.clip(notes[order, 3], 1, 127).astype(np.uint8) if n else np.zeros(0, np.uint8)
        slot = col(4, np.int64)
        self.chan = channels_of(slot, use10).astype(np.uint8) if n else np.zeros(0, np.uint8)
        self.port = (ports_of(slot, use10, ports).astype(np.uint8) if n and ports else np.zeros(n, np.uint8))
        if edo_first is not None and n:  # (31-EDO: the row decides channel + key, the note's colour doesn't)
            from files.playback import edo_code
            code = edo_code(notes[order, 2], edo_first)
            self.chan, self.key = (code & 15).astype(np.uint8), ((code >> 8) & 0x7F).astype(np.uint8)
            self.port = (code >> 24).astype(np.uint8)
            del code
        del slot, order
        self.bends = []  # (31-EDO: its channels' bends, as CC-line messages at a piece's start)
        if edo_first is not None:
            from files.playback import edo_bends
            self.bends = edo_bends(first=edo_first)
        self._finish(ccs)

    @classmethod
    def on_channels(cls, notes, chans, ccs, ppq, bpm, tempo=None):
        """A Song from rows (start, end, key, velocity) on stream channels chans (port x 16 + channel each; None =
        channel 1) and CC lines [(tick, message)] (channel | port << 24): the Hz preview's notes as it plays them
        (R7). Keys over 127 left out."""
        self = cls.__new__(cls)
        notes = np.asarray(notes)
        chans = np.zeros(len(notes), np.int64) if chans is None else np.asarray(chans, np.int64)
        if len(notes):
            keep = notes[:, 2] <= 127
            notes, chans = notes[keep], chans[keep]
        else:
            notes = np.zeros((0, 4), np.int64)
        self.edo_first, self.bends, self.ppq = None, [], ppq
        self.tempo = list(tempo) if tempo else [(0, int(round(60e6 / bpm)))]
        self.map = TimeMap(ppq, self.tempo)
        order = np.argsort(notes[:, 0], kind="stable") if len(notes) else np.zeros(0, np.int64)
        self.start, self.end = notes[order, 0].astype(np.int64), notes[order, 1].astype(np.int64)
        self.key = notes[order, 2].astype(np.uint8)
        self.vel = np.clip(notes[order, 3], 1, 127).astype(np.uint8)
        self.chan, self.port = (chans[order] & 15).astype(np.uint8), (chans[order] >> 4).astype(np.uint8)
        self._finish(ccs)
        return self

    def _finish(self, ccs):
        n = len(self.start)
        self.ccs = sorted(ccs, key=lambda e: e[0]) if ccs else []
        self.cc_ticks = np.array([t for t, _ in self.ccs], np.int64)
        # the longest note (held notes before a tick are among the ones starting up to that much earlier)
        self.longest = int((self.end - self.start).max()) if n else 0
        self.first = self.frame(int(self.start[0])) if n else 0
        self.last = self.frame(int(self.end.max())) if n else 0  # the frame the last note ends at
        self._bass = None  # (BASSMIDI's event list, made once: pieces made at once wait for the first to make it)
        self._bass_lock = threading.Lock()

    def __len__(self):
        return len(self.start)

    def frame(self, tick):
        return int(round(self.map.sec(tick) * RATE))

    def tick_at(self, frame):
        """The first tick at / after frame."""
        t = int(self.map.tick(frame / RATE))
        while self.frame(t) < frame:
            t += 1
        return t

    def _at(self, tick):
        """(tempo us in force at tick, the changes after it)."""
        before = [u for t, u in self.tempo if t <= tick]
        return before[-1], [(t, u) for t, u in self.tempo if t > tick]

    def piece_start(self, f0, last=None, pre=PRE):
        """(the tick a piece from frame f0 starts at, the tick notes count from: ones ending after it sound in it).
        PRE early; a note still held there = from its own start (at most HELD_PRE early: only the held notes come
        from that far back, so it costs little). Past the last note: held notes looked for PRE before the last end,
        so the song's ring after it is made too (a piece starting there would be silence otherwise). last = the
        whole song's last frame when this Song is only a part of it (a loaded MIDI's LoadedSong); pre = seconds
        early (a LoadedSong's dense walls: less)."""
        last = self.last if last is None else last
        t_pre = t0 = self.tick_at(max(0, min(f0, last) - int(pre * RATE)))
        lo = int(np.searchsorted(self.start, t0 - self.longest, "left"))
        held = lo + np.flatnonzero((self.start[lo:] < t0) & (self.end[lo:] > t0))
        if len(held):
            t0 = max(min(int(self.start[held].min()), t0), self.tick_at(max(0, min(f0, last) - int(HELD_PRE * RATE))))
        return t0, t_pre

    def pre_frames(self, f0):
        """Frames a piece from f0 starts early (BASSMIDI's render: synth.render pre)."""
        return f0 - self.frame(self.piece_start(f0)[0])

    def midi(self, f0, f1):
        """The piece f0 .. f1 (frames) as MIDI file bytes, + the frames to drop at its start (None: no notes sound
        in it)."""
        t_end = self.tick_at(f1 + int(POST_S * RATE))  # (a little past it: see POST_S)
        t0, t_pre = self.piece_start(f0)
        # (a PPQ over 32767 reads as SMPTE timing in a MIDI file: written as half the PPQ with half the tempo
        # value, so every tick keeps its time; hunt 2026-11-03, PPQ goes up to 65535)
        half = 2 if self.ppq > 32767 else 1
        tracks = self.tracks(t0, t_pre, t_end)
        if not tracks:
            return None, 0
        zero, seed = self.clock_at(t0) if half == 1 else (self.frame(t0), 0.0)
        tracks.insert(0, self.tempo_track(t0, t_end, (self.ppq // half) / self.ppq))
        head = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big")
        return PieceMidi(head + (self.ppq // half).to_bytes(2, "big") + b"".join(tracks), seed), f0 - zero

    def tempo_track(self, t0, t_end, scale=1.0):
        """The heard tempo from tick t0 (the piece's tick 0) to t_end as a track; scale = the tempo values' factor
        (an odd PPQ halved: the exact ratio, not 1/2)."""
        us, changes = self._at(t0)
        tempo_of = lambda u: b"\xff\x51\x03" + max(1, int(round(u * scale))).to_bytes(3, "big")  # noqa: E731
        return _track([(0, tempo_of(us))] + [(t - t0, tempo_of(u)) for t, u in changes if t < t_end])

    def clock_at(self, t0):
        """(the song frame a piece starting at tick t0 has its time 0 on, the clock seed it needs). Kestrel times
        notes as BASSMIDI's file player does (TempoTiming::Bassmidi): every note on a whole frame, and at every tempo
        change the clock snaps to a whole frame and carries the part over ("overshoot", in ticks) from the song's
        start on. A piece starting mid-song began with nothing carried: notes one frame off a whole render (single
        attacks -22..-33 dB, Shining Needle, 2026-11-06). Here Kestrel's clock is replayed (same sums in the same
        order, f64) up to t0; the seed (connector 0.9.0 clock_seed: the piece's clock starts with it carried) puts
        every note of the piece on the frame the whole render has it on. Tempo values as the piece writes them."""
        if t0 <= 0:
            return 0, 0.0
        states = self._clock_states()
        i = max(0, bisect.bisect_right([s[0] for s in states], t0) - 1)
        bt, bf, over, num, den = states[i]
        whole, rem = divmod((t0 - bt) * num, den)
        fr = rem / den - over * num / den
        zero = int(max(0.0, float(whole) + math.ceil(fr)))
        part = math.ceil(fr) - fr  # (frames from the piece's time 0 back to where the clock's ticks run from)
        return bf + zero, part * den / num

    def _clock_states(self):
        """[(tick of a tempo change, its whole frame, overshoot in ticks, num, den)] after each change: Kestrel's
        TempoClock (midi.rs set_tempo / frames_to, Bassmidi timing) run over the tempo list once."""
        got = getattr(self, "_clock", None)
        if got is not None:
            return got
        bt, bf, over = 0, 0, 0.0
        num, den = 500_000 * RATE, 1_000_000 * self.ppq  # (120 BPM until told otherwise)
        out = [(0, 0, 0.0, num, den)]
        for t, u in self.tempo:
            t = max(0, int(t))
            whole, rem = divmod((t - bt) * num, den)
            n = int(max(0.0, float(whole) + math.ceil(rem / den - over * num / den)))
            past = n * den - (t - bt) * num
            over = max(0.0, over + float(past) / float(num))
            bf += n
            bt = t
            num = max(1, int(round(u))) * RATE
            out.append((t, bf, over, num, den))
        self._clock = out
        return out

    def tracks(self, t0, t_pre, t_end, file_ports=()):
        """The notes + CC lines of a piece from tick t0 (its tick 0; notes ending after t_pre sound in it) to t_end
        as MTrk chunks, one per port ([] = nothing there). file_ports = ports a loaded MIDI uses: their channel 10
        stays drums (its own, as live play has it)."""
        lo = int(np.searchsorted(self.start, t0 - self.longest, "left"))
        hi = int(np.searchsorted(self.start, t_end, "left"))
        idx = lo + np.flatnonzero(self.end[lo:hi] > t_pre)  # (the notes sounding from PRE early on)
        c0, c1 = int(np.searchsorted(self.cc_ticks, t0, "left")), int(np.searchsorted(self.cc_ticks, t_end, "left"))
        if not len(idx) and c0 == c1:
            return []
        # every event as arrays (NumPy, never a Python object a note: a dense piece holds millions):
        # tick, order on a shared tick (ends 0, CC lines 1, starts 2), status, data 1, data 2, port
        n = len(idx)
        from files.playback import cc_before
        cc = [(0, m) for m in self.bends + cc_before(self.ccs, t0)] + [(t - t0, m) for t, m in self.ccs[c0:c1]]
        cm = np.array([m for _, m in cc], np.int64)
        tick = np.concatenate([self.end[idx] - t0, np.maximum(self.start[idx], t0) - t0,
                               np.array([t for t, _ in cc], np.int64)])
        order = np.concatenate([np.zeros(n, np.int8), np.full(n, 2, np.int8), np.ones(len(cc), np.int8)])
        ch = self.chan[idx]
        st = np.concatenate([0x80 | ch, 0x90 | ch, ((cm & 0xF0) | (cm & 15)).astype(np.uint8)])
        d1 = np.concatenate([self.key[idx], self.key[idx], ((cm >> 8) & 0x7F).astype(np.uint8)])
        d2 = np.concatenate([np.zeros(n, np.uint8), self.vel[idx], ((cm >> 16) & 0x7F).astype(np.uint8)])
        port = np.concatenate([self.port[idx], self.port[idx], (cm >> 24).astype(np.uint8)])
        del cm, ch
        tracks = []
        used = np.unique(port).tolist()
        moved = {}
        if file_ports and self.edo_first is None:
            # (a loaded MIDI's ports: your shapes moved to ports it leaves free, so they never take its instruments /
            # volume / drums and sound as in Built-in BASSMIDI: piano, channel 10 melodic; hunt 2026-11-03. All 16
            # used: shared, as before)
            free = [q for q in range(16) if q not in file_ports and q not in used]
            for p in used:
                if p % 16 in file_ports and free:
                    moved[p] = free.pop(0)
        for p in used:
            sel = np.flatnonzero(port == p)
            sel = sel[np.lexsort((order[sel], tick[sel]))]
            q = moved.get(p, p)
            head = [(0, b"\xff\x21\x01" + bytes([q & 127]))] + ([] if q % 16 in file_ports else [(0, _MELODIC_10)])
            tracks.append(_track(head, _messages(tick[sel], st[sel], d1[sel], d2[sel])))
        return tracks

    def bass_events(self):
        """The whole song as BASSMIDI's event list (synth.events' order; each port's channels as the stream's
        channels port x 16 + channel; channel 10s melodic)."""
        with self._bass_lock:
            return self._bass_now()

    def _bass_now(self):
        if self._bass is None:
            from files import synth
            rows = np.stack([self.start, self.end, self.key, self.vel], axis=1) if len(self) else \
                np.zeros((0, 4), np.int64)
            chans = self.port.astype(np.int64) * 16 + self.chan
            cc = [(t, m & 0xF0, (m >> 8) & 0x7F, (m >> 16) & 0x7F, (m >> 24) * 16 + (m & 15))
                  for t, m in [(0, b) for b in self.bends] + self.ccs]
            extra = synth.cc_events(cc)
            used = sorted({int(c) for c in np.unique(chans)} | {int(c[4]) for c in cc})
            drums = np.array([(synth._EV_DRUMS, 0, c, 0, 0) for c in used if c % 16 == 9], synth.EVENT)
            extra = np.concatenate([drums, extra]) if len(drums) else extra
            tempo = self.tempo if len(self.tempo) > 1 else None
            self._bass = synth.events(rows, self.ppq, 60e6 / self.tempo[0][1], chans, extra, True, tempo)
        return self._bass


_FIELDS = ("start", "end", "key", "vel", "chan", "port")


def _ends_tick(song):
    return int(song.end.max()) if len(song) else 0


def same_tempo(a, b):
    """True when two Songs' tempo is the same up to where either one's notes end (the heard tempo list is cut at the
    song's end, which moves with edits: points past both songs' notes don't count)."""
    if a.tempo == b.tempo:
        return True
    far = max(_ends_tick(a), _ends_tick(b))
    return [p for p in a.tempo if p[0] <= far] == [p for p in b.tempo if p[0] <= far]


def changed_ticks(a, b):
    """(first tick, last tick) of what differs between two Songs with the same tempo (notes in start order: from
    the first note that differs to the last, both songs' notes there, their ends included; a changed CC line
    reaches to the song's end). None = the same sound."""
    lo = hi = None
    na, nb = len(a), len(b)
    n = min(na, nb)
    if na != nb or any(not np.array_equal(getattr(a, k), getattr(b, k)) for k in _FIELDS):
        diff = np.zeros(n, bool)
        for k in _FIELDS:
            diff |= getattr(a, k)[:n] != getattr(b, k)[:n]
        first = int(np.argmax(diff)) if diff.any() else n
        rdiff = np.zeros(n, bool)
        for k in _FIELDS:
            rdiff |= getattr(a, k)[na - n:][::-1] != getattr(b, k)[nb - n:][::-1]
        same_end = int(np.argmax(rdiff)) if rdiff.any() else n
        same_end = min(same_end, n - first)
        for s in (a, b):
            part = slice(first, len(s) - same_end)
            if part.stop > part.start:
                t0, t1 = int(s.start[part].min()), int(s.end[part].max())
                lo = t0 if lo is None else min(lo, t0)
                hi = t1 if hi is None else max(hi, t1)
    if a.ccs != b.ccs:
        k = 0
        while k < min(len(a.ccs), len(b.ccs)) and a.ccs[k] == b.ccs[k]:
            k += 1
        t0 = min(t for t in ([a.ccs[k][0]] if k < len(a.ccs) else []) + ([b.ccs[k][0]] if k < len(b.ccs) else []))
        t1 = max(_ends_tick(a), _ends_tick(b), t0)
        lo = t0 if lo is None else min(lo, t0)
        hi = t1 if hi is None else max(hi, t1)
    return None if lo is None else (lo, hi)


CARD_LOCK = threading.Lock()  # (one piece on the graphics card at a time: Rendered ahead's + the Hz preview's)
PIECE_PREFIX = "spiderweb-piece-"  # (pieces' MIDI files in the temp folder, deleted once made)
OLD_PIECE_S = 600  # a piece file older than this is a leftover (Spiderweb closed mid-piece): deleted
_cleaned = []


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _clean_old_pieces():
    """Once a run: piece files left in the temp folder by a Spiderweb closed mid-piece (hunt 2026-11-03). Only old
    ones: another Spiderweb (another folder) may be making one right now."""
    if _cleaned:
        return
    _cleaned.append(True)
    folder, now = tempfile.gettempdir(), time.time()
    try:
        names = [n for n in os.listdir(folder) if n.startswith(PIECE_PREFIX) and n.endswith(".mid")]
    except OSError:
        return
    for n in names:
        p = os.path.join(folder, n)
        try:
            if now - os.path.getmtime(p) > OLD_PIECE_S:
                os.remove(p)
        except OSError:
            pass


def _card_one(song, engine, opts, fonts, card, f0, f1, cancel, progress, plain=False, built=None):
    """One piece on the card (card_piece's part): float32 (frames, 2), maybe shorter (silence at its end left out),
    or None when cancelled. plain = never counted (a re-cut part, see card_piece). built() = called once its MIDI is
    made (the next piece may start making its own: Maker, PLAN E3)."""
    from files import kestrel
    # (built() after the MIDI only: a counted try that has to be re-cut plain (TooBigPlain) leaves it to the re-cut's
    # last part, else the next piece was made beside the plain parts, hunt 11-04; an error = _work sets it at its end)
    if not plain and engine == "merlin" and getattr(song, "counts_notes", False) and kestrel.repeat_marks():
        kept = kestrel.kept_for(fonts, card)  # (the soundfonts' longest release: how far back ended notes still
        if kept is not None:                  # sound into the next piece, LoadedSong.release_s)
            song.release_s = kept.longest_release()
        data, skip = song.midi(f0, f1, counted=True, cancel=cancel)  # (stacks of identical notes sent once, PLAN D2)
        if built is not None:
            built()
        if cancel.is_set():  # (stopped while it was made, PLAN E)
            return None
    else:
        data, skip = song.midi(f0, f1)
        if built is not None:
            built()
    if data is None:  # (nothing sounds here: silence, no piece)
        return np.zeros((f1 - f0, 2), np.float32)
    with CARD_LOCK:
        if cancel.is_set():
            return None
        loaded = kestrel.loaded_for(fonts, card)
        _clean_old_pieces()  # (files older Spiderwebs left: pieces go to the connector as bytes now, user 11-03)
        o = {"block": opts["block"], "clock_seed": getattr(data, "clock_seed", 0.0)}
        if engine == "kestrel":
            o.update(voices=opts["voices"], interpolation=opts["interpolation"], min_velocity=opts["min_velocity"])
        piece = kestrel.Piece(loaded, data, engine, skip, f1 - f0, **o)
        del data  # (the connector has its own copy: ours let go before the long wait)
        try:
            while True:
                state = piece.wait(0.05)
                if cancel.is_set():
                    piece.cancel()
                    return None
                if state != kestrel.RUNNING:
                    break
                if progress:
                    progress(piece.status()["progress"])
            if state == kestrel.FAILED:
                from files.synth import SynthError
                raise SynthError("playback.kestrel_failed", err=piece.error() or "?")
            if state != kestrel.DONE:
                return None
            return piece.audio()
        finally:
            piece.close()


def card_piece(song, engine, opts, fonts, card, f0, f1, cancel, progress=None, piece_most=None, one=None,
               built=None):
    """(a piece's thread) Song frames f0 .. f1 made on the graphics card by "merlin" / "kestrel" with the soundfonts
    already there (kestrel.loaded_for: read once, kept); opts = App.ahead_opts. Returns (float32 (frames, 2) or None
    when cancelled, the frame the ring died away at when it did in this piece, else None). progress(0..1).
    piece_most = bytes a loaded MIDI's piece copies at most (Maker.piece_most): more = made in parts.
    one(a, b, cancel, progress) = what makes one part instead of the card (BASSMIDI with a loaded MIDI, L4).
    built() = called once the last part's MIDI is made (_card_one)."""
    if one is None:
        def on_card(a, b, c, p, plain=False):
            return _card_one(song, engine, opts, fonts, card, a, b, c, p, plain, built if b == f1 else None)

        def one(a, b, c, p):
            from files.ahead_loaded import TooBigPlain
            try:
                return on_card(a, b, c, p)
            except TooBigPlain:  # (a counted piece that has to go plain, PLAN D2: re-cut to plain sizes, each
                parts = []      # part plain: counted again it raised again, uncaught = playing stopped)
                for a2, b2 in song.spans(a, b, PIECE_MOST):
                    got = on_card(a2, b2, c, None, True)
                    if got is None:
                        return None
                    if len(got) < b2 - a2:
                        got = np.concatenate([got, np.zeros((b2 - a2 - len(got), 2), np.float32)])
                    parts.append(got)
                return np.concatenate(parts)
    spans = song.spans(f0, f1, piece_most) if piece_most and hasattr(song, "spans") else [(f0, f1)]
    if len(spans) == 1:
        out = one(f0, f1, cancel, progress)
    else:  # (M1: a dense wall's block in parts, one after another, joined: one piece never takes GBs)
        parts = []
        for k, (a, b) in enumerate(spans):
            got = one(a, b, cancel, progress and (lambda p, k=k: progress((k + p) / len(spans))))
            if got is None:
                return None, None
            if len(got) < b - a:
                got = np.concatenate([got, np.zeros((b - a - len(got), 2), np.float32)])
            parts.append(got)
        out = np.concatenate(parts)
        del parts
    if out is None:
        return None, None
    end = None
    if out is not None and len(out) < f1 - f0 and f0 + len(out) >= song.last:  # (the ring died away)
        end = f0 + len(out)
    elif out is not None and len(out) < f1 - f0:  # (silence at the piece's end left out: put back)
        out = np.concatenate([out, np.zeros((f1 - f0 - len(out), 2), np.float32)])
    elif out is not None and f1 > song.last:  # (Merlin hands back the whole piece: the ring's end found here)
        end = _died_at(out, f0, song.last)
    return out, end


SILENT = 1e-5  # (-100 dB: the ring counts as died away below it to the piece's end)


def _died_at(out, f0, last):
    """The song frame the sound fell silent at for good in this piece (after the last note ends), or None."""
    start = max(0, last - f0)
    if start >= len(out):
        return None
    loud = np.flatnonzero(np.abs(out[start:]).max(axis=1) > SILENT)
    at = start + (int(loud[-1]) + 1 if len(loud) else 0)
    return f0 + at if at < len(out) else None


class _Job:
    """One piece being made: blocks first .. first + count - 1."""

    def __init__(self, first, count, vers):
        self.first, self.count, self.vers = first, count, vers  # (each block's version when it started)
        self.cancel = threading.Event()
        self.progress = 0.0  # 0..1 (the grey clears along it)
        self.result = self.error = None  # (audio float32 (frames, 2): count blocks or fewer at the song's end)
        self.end = None  # the song frame the sound died away at, when it did in this piece
        self.took = 0.0
        self.lowered = False  # (made with a lowered voice limit)
        self.finished = False
        self.built = threading.Event()  # (its MIDI made: the piece right after it may make its own, PLAN E3)
        self.prev = None  # (the job right before it, still being made when it started)


class Maker:
    """Makes the song's blocks ahead of an anchor frame (the play line). engine = "merlin" / "kestrel" /
    "bassmidi"; opts = App.ahead_opts; fonts = the soundfont list; card = the shared graphics card; synth = a
    files.synth.Synth with the soundfonts open (BASSMIDI only)."""

    def __init__(self, engine, opts, fonts, card="", synth=None):
        self.engine, self.opts, self.fonts, self.card, self.synth = engine, dict(opts), list(fonts), card, synth
        self.song = None
        self.span = (0, 0)  # frames with sound in them: first note .. the ring's end
        self.blocks = {}  # block -> audio (BLOCK frames each; the span's end ends the sound)
        self.ver = {}  # block -> version (a change throws away a piece still making it)
        self.jobs = []
        self.threads = []
        self.ends = {}  # block -> the frame the sound died away at in it
        self.took = []  # seconds the last pieces took, with their blocks: [(seconds, blocks)]
        self.error = None  # the first piece's error text (the caller says it)
        self._lock = threading.Lock()
        self._free = (0.0, None)  # (when free memory was last asked, the answer)
        self.lowered = False  # (Lower it by itself: new pieces made with a lowered voice limit, ahead_play)
        self.low = set()  # blocks made lowered (R5: made again at the full limit once calm)
        self._to_card = None  # (the soundfonts' trip to the graphics card: a Thread, False = already there)

    def held_bytes(self):
        return sum(b.nbytes for b in list(self.blocks.values()))

    def room(self):
        """MEMORY (user 2026-11-03): seconds ahead the made sound may take: what's held now + MEMORY_SHARE of the
        free memory (asked every FREE_EVERY s), less BEHIND. 1 s = BYTES_A_SECOND (0.38 MB): 300 s = 115 MB."""
        from files.system import free_memory
        now = time.monotonic()
        if now - self._free[0] > FREE_EVERY:
            self._free = (now, free_memory())
        free = self._free[1]
        if free is None:
            return float("inf")
        return max(BLOCK_S, (self.held_bytes() + free * MEMORY_SHARE) / BYTES_A_SECOND - BEHIND)

    def piece_most(self):
        """Bytes a loaded MIDI's piece may copy: PIECE_MOST, less with little free memory (Merlin's own working
        memory is ~5x the piece, measured: a 249 MB piece added 1.3 GB; the copy + its read ~3x more)."""
        self.room()  # (free memory asked again when due)
        free = self._free[1]
        if self.engine == "merlin" and getattr(self.song, "counted_here", lambda: False)():
            return COUNTED_MOST if free is None else max(PIECE_LEAST, min(COUNTED_MOST, int(free / 2)))
        return PIECE_MOST if free is None else max(PIECE_LEAST, min(PIECE_MOST, int(free / 16)))

    # ------------------------------------------------------------ the song

    def set_song(self, song):
        """A new song: every block made again (R5 makes only the changed stretch)."""
        self.song = song
        self.clear()
        self.span = (song.first, song.last + int(TAIL * RATE)) if song is not None and len(song) else (0, 0)

    def update_song(self, song):
        """(R5) The song changed: only the stretch that changed (+ its ring, TAIL) is thrown away and made again;
        another PPQ / tempo = everything. Returns True when anything was thrown away."""
        old = self.song
        if old is None or song is None or old.ppq != song.ppq or old.edo_first != song.edo_first or \
                not same_tempo(old, song):
            self.set_song(song)
            return True
        lo_old, lo_new = getattr(old, "lm", None), getattr(song, "lm", None)
        if lo_old is not None or lo_new is not None:  # (a loaded MIDI: its part the same = your shapes compared)
            if lo_old is None or lo_new is None or old.key != song.key:
                self.set_song(song)  # (L3 makes only from the play line)
                return True
            span = changed_ticks(old.own, song.own)
        else:
            span = changed_ticks(old, song)
        if span is None:
            self.song = song  # (the same sound: the newer copy kept)
            return False
        self.song = song
        self.span = (song.first, song.last + int(TAIL * RATE)) if len(song) else (0, 0)
        if not len(song):
            self.clear()
            return True
        self.clear(song.frame(span[0]), song.frame(span[1]) + int(TAIL * RATE))
        return True

    def clear(self, lo=None, hi=None):
        """Throws away the blocks between frames lo and hi (all without them) and stops making them."""
        with self._lock:
            for i in list(self.ver.keys() | self.blocks.keys() | self.ends.keys()):
                if lo is None or (i + 1) * BLOCK > lo and i * BLOCK < hi:
                    self._drop(i)
            for job in self.jobs:
                if lo is None or (job.first + job.count) * BLOCK > lo and job.first * BLOCK < hi:
                    job.cancel.set()
                    for i in range(job.first, job.first + job.count):
                        self._drop(i)
            self.jobs = [j for j in self.jobs if not j.cancel.is_set()]
        if self.song is not None and len(self.song):
            self._set_end()

    def _drop(self, i):
        self.ver[i] = self.ver.get(i, 0) + 1
        self.blocks.pop(i, None)
        self.ends.pop(i, None)
        self.low.discard(i)

    def _set_end(self):
        """The sound ends where a piece found the ring died away, else TAIL after the last note. (An end found
        before the last note = an older song's, from before notes were added later: not used.)"""
        last = self.song.last
        self.span = (self.span[0], int(min([last + int(TAIL * RATE)] + [e for e in self.ends.values() if e >= last])))

    # ------------------------------------------------------------ what to make

    def wanted(self, anchor, ahead_s):
        """Blocks from the anchor's block to ahead_s seconds past it (within the span), in the order needed."""
        lo, hi = self.span
        if hi <= lo:
            return []
        at = max(anchor, lo)
        if at >= hi:
            return []
        return list(range(at // BLOCK, (min(at + int(ahead_s * RATE), hi - 1)) // BLOCK + 1))

    def _busy(self, i):
        return any(j.first <= i < j.first + j.count for j in self.jobs)

    def step(self, anchor, ahead_s):
        """(window's thread, every tick) Takes in finished pieces, throws away far blocks / pieces, starts new
        pieces for the missing blocks nearest the anchor. Returns True when something changed (the grey)."""
        changed = self._collect()
        want = self.wanted(anchor, min(ahead_s, self.room()))
        keep = set(want)
        with self._lock:
            # (behind the play line past BEHIND, or past the window: kept up to KEPT_MOST_S (and what free memory
            # allows), the farthest from the play line thrown away first)
            far = [i for i in self.blocks
                   if i not in keep and not (anchor - BEHIND * RATE <= (i + 1) * BLOCK and i * BLOCK <= anchor)]
            budget = min(KEPT_MOST_S, max(0.0, self.room() - len(keep) * BLOCK_S))
            far.sort(key=lambda i: abs(i * BLOCK - anchor))
            for i in far[int(budget // BLOCK_S):]:
                del self.blocks[i]
                self.low.discard(i)
                changed = True
            for job in self.jobs:
                if not any(i in keep for i in range(job.first, job.first + job.count)):
                    job.cancel.set()
            self.jobs = [j for j in self.jobs if not j.cancel.is_set()]
        self.threads = [t for t in self.threads if t.is_alive()]
        if self.song is None or self.error:
            return changed
        if want and self.engine in ("merlin", "kestrel") and self._to_card is None:
            self._warm_card()
        most = GPU_WORKERS if self.engine in ("merlin", "kestrel") else CPU_WORKERS
        chained = self.engine == "merlin" and getattr(self.song, "counted_here", lambda: False)()
        if chained:
            most = OVERLAP_WORKERS  # (the next piece's MIDI made while Merlin plays this one: the card still one at a time)
        k = 0
        while k < len(want) and len(self.threads) < most:
            i = want[k]
            if i in self.blocks or self._busy(i):
                k += 1
                continue
            # a run of missing blocks: the first piece of it short (a quick start), later ones RUN blocks
            n = FIRST_RUN if (k == 0 or want[k - 1] in self.blocks or self._busy(want[k - 1])) and \
                i == want[0] else RUN
            run = [i]
            while len(run) < n and k + len(run) < len(want):
                j = want[k + len(run)]
                if j != run[-1] + 1 or j in self.blocks or self._busy(j):
                    break
                run.append(j)
            fit = getattr(self.song, "blocks_fit", None)  # (a loaded MIDI: dense parts in shorter pieces, memory)
            cap = self.piece_most() if fit is not None else None
            if fit is not None and len(run) > 1:
                run = run[:fit(i, len(run), cap)]
            heavy = 0
            if fit is not None and most > 1:
                # (BASSMIDI makes several at once: heavy pieces wait while the ones being made already copy BUSY_MOST
                # caps; a smaller cap each split dense walls into dozens of parts, each reading it all again: 2 s
                # took 394 s instead of 43, L5)
                heavy = self.song.bytes_in(i * BLOCK, (i + len(run)) * BLOCK)
                with self._lock:
                    busy = sum(getattr(j, "heavy", 0) for j in self.jobs)
                if self.jobs and busy + heavy > BUSY_MOST * cap:
                    break
            job = _Job(i, len(run), [self.ver.get(j, 0) for j in run])
            job.heavy = heavy
            job.most = cap  # (a block still over it: made in parts, card_piece)
            job.lowered = self.lowered
            with self._lock:
                # (only counted Merlin pieces wait for the one before: BASSMIDI makes several side by side, hunt 11-04)
                job.prev = next((j for j in self.jobs if j.first + j.count == i and not j.finished),
                                None) if chained else None
                self.jobs.append(job)
            t = threading.Thread(target=self._work, args=(job, self.song), daemon=True)
            t.start()
            self.threads.append(t)
            k += len(run)
            changed = True
        return changed

    def _warm_card(self):
        """The soundfonts sent to the graphics card on a thread of their own, beside the first piece's MIDI (hunt
        11-04: they went only once that MIDI was made, a dense wall's lead-up = a minute later; the status line
        said "putting the soundfont on the graphics card" all along). A failure is left to the piece (it says it)."""
        from files import kestrel
        if kestrel.kept_for(self.fonts, self.card) is not None:
            self._to_card = False
            return

        def go():
            try:
                kestrel.loaded_for(self.fonts, self.card)
            except Exception:  # (said by the piece that needs it)
                pass
        self._to_card = threading.Thread(target=go, daemon=True, name="soundfonts to the card")
        self._to_card.start()

    def card_loading(self):
        """True while the soundfonts are being sent to the graphics card (_warm_card)."""
        return bool(self._to_card) and self._to_card.is_alive()

    def _collect(self):
        changed = False
        with self._lock:
            done = [j for j in self.jobs if j.finished]
            self.jobs = [j for j in self.jobs if not j.finished]
            for job in done:
                changed = True
                if job.error:
                    self.error = self.error or job.error
                    continue
                if job.result is None:
                    continue
                for k in range(job.count):
                    i = job.first + k
                    if job.vers[k] != self.ver.get(i, 0):
                        continue  # (changed while it was made)
                    part = job.result[k * BLOCK:(k + 1) * BLOCK]
                    if len(part) < BLOCK:  # (the sound died away in it: silence to the block's end. ALWAYS: a short
                        # block read as the song's end stopped playing before notes added past it, hunt 2026-11-03;
                        # only the span's end ends the sound)
                        part = np.concatenate([part, np.zeros((BLOCK - len(part), 2), np.float32)])
                    # (its own copy: a block kept must not hold the whole piece's buffer)
                    self.blocks[i] = np.ascontiguousarray(part).copy() if job.count > 1 else part
                    (self.low.add if job.lowered else self.low.discard)(i)
                if job.end is not None:
                    self.ends[job.first] = job.end
                self.took = (self.took + [(job.took, job.count)])[-8:]
        if done and self.song is not None:
            self._set_end()
        return changed

    # ------------------------------------------------------------ making (a piece's own thread)

    def _work(self, job, song):
        started = time.perf_counter()
        f0 = job.first * BLOCK
        f1 = min((job.first + job.count) * BLOCK, max(self.span[1], f0 + 1))
        prev = job.prev
        job.prev = None  # (no chain of finished jobs kept)
        while prev is not None and not (prev.built.is_set() or prev.finished or prev.cancel.is_set() or
                                        job.cancel.is_set()):
            prev.built.wait(0.05)  # (its MIDI first: it hands this piece the notes still sounding, PLAN E3)
        try:
            if self.engine == "bassmidi":
                out = self._bass(job, song, f0, f1)
            else:
                out = self._card(job, song, f0, f1)
            if out is not None and not job.cancel.is_set():
                if not np.isfinite(out).all():  # (never a loud crack from a broken piece)
                    out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
                job.result = out
        except Exception as e:  # (said by the caller; the traceback goes to the log)
            from files.synth import SynthError
            job.error = str(e) if isinstance(e, SynthError) else repr(e)
            if not isinstance(e, SynthError):
                errors.write_log(type(e), e, e.__traceback__, "Rendered ahead piece")
        job.took = time.perf_counter() - started
        job.built.set()
        job.finished = True

    def _bass(self, job, song, f0, f1):
        if getattr(song, "lm", None) is not None:  # (a loaded MIDI: the card's piece files, read by BASSMIDI, L4)
            voices = self.opts["bass_voices"]

            def one(a, b, cancel, progress):
                data, skip = song.midi(a, b)
                if data is None:  # (nothing sounds here: silence)
                    return np.zeros((b - a, 2), np.float32)
                return self.synth.render_file(data, skip, b - a, voices, a, cancel,
                                              progress and (lambda n: progress(n / max(1, b - a))))
            out, end = card_piece(song, self.engine, self.opts, self.fonts, self.card, f0, f1, job.cancel,
                                  lambda p: setattr(job, "progress", p), getattr(job, "most", None), one)
            job.end = end
            return out
        stats = {}
        out = self.synth.render(song.bass_events(), song.ppq, f0, f1 - f0, self.opts["bass_voices"], False,
                                job.cancel, lambda n: setattr(job, "progress", n / max(1, f1 - f0)), stats,
                                pre=song.pre_frames(f0))  # (held notes sound on: from their start, as on the card)
        if out is not None and stats.get("end") is not None:
            job.end = int(stats["end"])
            out = out[:max(0, job.end - f0)]
        return out

    def _card(self, job, song, f0, f1):
        out, end = card_piece(song, self.engine, self.opts, self.fonts, self.card, f0, f1, job.cancel,
                              lambda p: setattr(job, "progress", p), getattr(job, "most", None), built=job.built.set)
        job.end = end
        return out

    # ------------------------------------------------------------ playing / grey

    def take(self, at, n):
        """(sound's thread) Up to n frames of sound from song frame at: None = not made yet, empty = the end."""
        lo, hi = self.span
        if at >= hi:
            return np.zeros((0, 2), np.float32)
        if at < lo:  # (before the first note: silence)
            return np.zeros((min(n, lo - at), 2), np.float32)
        got = self.blocks.get(at // BLOCK)
        if got is None:
            return None
        start = at - (at // BLOCK) * BLOCK
        return got[start:start + min(n, hi - at)]

    def grey(self, lo, hi):
        """[(from, to)] song frames between lo and hi not made yet (a piece being made clears along its progress)."""
        lo, hi = max(lo, self.span[0]), min(hi, self.span[1])
        spans = []
        if hi <= lo:
            return spans
        with self._lock:
            jobs = list(self.jobs)
        for i in range(lo // BLOCK, (hi - 1) // BLOCK + 1):
            if i in self.blocks:
                continue
            a, b = max(lo, i * BLOCK), min(hi, (i + 1) * BLOCK)
            job = next((j for j in jobs if j.first <= i < j.first + j.count), None)
            if job is not None:  # (made in order through the piece)
                done = job.first * BLOCK + job.progress * job.count * BLOCK
                a = max(a, int(done))
            if b <= a:
                continue
            if spans and spans[-1][1] >= a:
                spans[-1][1] = b
            else:
                spans.append([a, b])
        return [tuple(s) for s in spans]

    def made_ahead(self, at):
        """Seconds of sound made in a row from frame at (the R1c lowering + the first batch look at it; before the
        first note: from it, nothing is made there)."""
        at = max(at, self.span[0])
        i, n = at // BLOCK, 0
        while i + n in self.blocks:
            n += 1
        return max(0.0, ((i + n) * BLOCK - at) / RATE) if n else 0.0

    @property
    def speed(self):
        """How many times faster than real time pieces are made (None = not known yet)."""
        if not self.took:
            return None
        return sum(c for _, c in self.took) * BLOCK_S / max(1e-6, sum(s for s, _ in self.took))

    def close(self):
        """Stops every piece (their threads end by themselves) and lets the blocks go."""
        with self._lock:
            for job in self.jobs:
                job.cancel.set()
            self.jobs = []
        self.blocks.clear()
        self.song = None
