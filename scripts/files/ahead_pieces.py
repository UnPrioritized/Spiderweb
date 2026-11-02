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
- BASSMIDI (files/synth.py Synth.render): straight from the song's event list (it does its own PRE).
Each piece is made with the limiter OFF: Spiderweb's own runs over the joined pieces (R3).

Thread rules: Maker.step() runs on the window's thread (quick: it only looks at jobs and starts threads); the
making runs in threads of its own; Maker.take() is called from the sound's thread."""

import os
import tempfile
import threading
import time

import numpy as np

from files import errors
from files.playback import TimeMap
from files.synth import RATE

BLOCK_S = 2.0  # seconds a block (the grey / remake / play unit)
BLOCK = int(BLOCK_S * RATE)
FIRST_RUN = 1  # blocks in the first piece of a run of missing blocks next to the play line (a quick start)
RUN = 5  # blocks in a later piece (10 s)
PRE = 1.0  # seconds a piece starts early (dropped)
HELD_PRE = 30.0  # ... at most, for a note still held there (8 s: an 18 s held note was -14 dB off at 12 s)
TAIL = 30.0  # seconds of ring after the last note at most (a piece finding the sound died away ends it sooner)
BEHIND = 10.0  # seconds of made blocks kept behind the play line (further back = thrown away)
BYTES_A_SECOND = RATE * 2 * 4  # (made sound: stereo float32)
MEMORY_SHARE = 0.25  # the made sound never takes more than this share of the free memory (less is made ahead)
FREE_EVERY = 2.0  # seconds between asking the system for free memory
GPU_WORKERS = 1  # Merlin / Kestrel pieces made at once (one graphics card)
CPU_WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))  # BASSMIDI pieces made at once (one core each)
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
    tempo = heard [(tick, us)] from tick 0 (None = one tempo: bpm). Keys over 127 are left out (31-EDO: R6)."""

    def __init__(self, notes, ccs, ports, use10, ppq, bpm, tempo=None):
        from notes.engine import channels_of, ports_of
        notes = np.asarray(notes)
        if len(notes):
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
        del slot, order
        self.ccs = sorted(ccs, key=lambda e: e[0]) if ccs else []
        self.cc_ticks = np.array([t for t, _ in self.ccs], np.int64)
        # the longest note (held notes before a tick are among the ones starting up to that much earlier)
        self.longest = int((self.end - self.start).max()) if n else 0
        self.first = self.frame(int(self.start[0])) if n else 0
        self.last = self.frame(int(self.end.max())) if n else 0  # the frame the last note ends at
        self._bass = None  # (BASSMIDI's event list, made once)

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

    def midi(self, f0, f1):
        """The piece f0 .. f1 (frames) as MIDI file bytes, + the frames to drop at its start (None: no notes sound
        in it)."""
        t_end = self.tick_at(f1)
        t_pre = t0 = self.tick_at(max(0, f0 - int(PRE * RATE)))
        lo = int(np.searchsorted(self.start, t0 - self.longest, "left"))
        held = lo + np.flatnonzero((self.start[lo:] < t0) & (self.end[lo:] > t0))
        if len(held):  # (a note still held: from its start, at most HELD_PRE early; only the held notes come
            # from that far back, so it costs little)
            t0 = max(min(int(self.start[held].min()), t0), self.tick_at(max(0, f0 - int(HELD_PRE * RATE))))
        hi = int(np.searchsorted(self.start, t_end, "left"))
        idx = lo + np.flatnonzero(self.end[lo:hi] > t_pre)  # (the notes sounding from PRE early on)
        c0, c1 = int(np.searchsorted(self.cc_ticks, t0, "left")), int(np.searchsorted(self.cc_ticks, t_end, "left"))
        if not len(idx) and c0 == c1:
            return None, 0
        skip = f0 - self.frame(t0)
        us, changes = self._at(t0)
        tempo = [(0, b"\xff\x51\x03" + int(us).to_bytes(3, "big"))]
        tempo += [(t - t0, b"\xff\x51\x03" + int(u).to_bytes(3, "big")) for t, u in changes if t < t_end]
        # every event as arrays (NumPy, never a Python object a note: a dense piece holds millions):
        # tick, order on a shared tick (ends 0, CC lines 1, starts 2), status, data 1, data 2, port
        n = len(idx)
        from files.playback import cc_before
        cc = [(0, m) for m in cc_before(self.ccs, t0)] + [(t - t0, m) for t, m in self.ccs[c0:c1]]
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
        tracks = [_track(tempo)]
        for p in np.unique(port).tolist():
            sel = np.flatnonzero(port == p)
            sel = sel[np.lexsort((order[sel], tick[sel]))]
            head = [(0, b"\xff\x21\x01" + bytes([p & 127])), (0, _MELODIC_10)]
            tracks.append(_track(head, _messages(tick[sel], st[sel], d1[sel], d2[sel])))
        del tick, order, st, d1, d2, port
        head = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(tracks).to_bytes(2, "big")
        return head + self.ppq.to_bytes(2, "big") + b"".join(tracks), skip

    def bass_events(self):
        """The whole song as BASSMIDI's event list (synth.events' order; each port's channels as the stream's
        channels port x 16 + channel; channel 10s melodic)."""
        if self._bass is None:
            from files import synth
            rows = np.stack([self.start, self.end, self.key, self.vel], axis=1) if len(self) else \
                np.zeros((0, 4), np.int64)
            chans = self.port.astype(np.int64) * 16 + self.chan
            cc = [(t, m & 0xF0, (m >> 8) & 0x7F, (m >> 16) & 0x7F, (m >> 24) * 16 + (m & 15)) for t, m in self.ccs]
            extra = synth.cc_events(cc)
            used = sorted({int(c) for c in np.unique(chans)} | {int(c[4]) for c in cc})
            drums = np.array([(synth._EV_DRUMS, 0, c, 0, 0) for c in used if c % 16 == 9], synth.EVENT)
            extra = np.concatenate([drums, extra]) if len(drums) else extra
            tempo = self.tempo if len(self.tempo) > 1 else None
            self._bass = synth.events(rows, self.ppq, 60e6 / self.tempo[0][1], chans, extra, True, tempo)
        return self._bass


class _Job:
    """One piece being made: blocks first .. first + count - 1."""

    def __init__(self, first, count, vers):
        self.first, self.count, self.vers = first, count, vers  # (each block's version when it started)
        self.cancel = threading.Event()
        self.progress = 0.0  # 0..1 (the grey clears along it)
        self.result = self.error = None  # (audio float32 (frames, 2): count blocks or fewer at the song's end)
        self.end = None  # the song frame the sound died away at, when it did in this piece
        self.took = 0.0
        self.finished = False


class Maker:
    """Makes the song's blocks ahead of an anchor frame (the play line). engine = "merlin" / "kestrel" /
    "bassmidi"; opts = App.ahead_opts; fonts = the soundfont list; card = the shared graphics card; synth = a
    files.synth.Synth with the soundfonts open (BASSMIDI only)."""

    def __init__(self, engine, opts, fonts, card="", synth=None):
        self.engine, self.opts, self.fonts, self.card, self.synth = engine, dict(opts), list(fonts), card, synth
        self.song = None
        self.span = (0, 0)  # frames with sound in them: first note .. the ring's end
        self.blocks = {}  # block -> audio (BLOCK frames, fewer for the song's last)
        self.ver = {}  # block -> version (a change throws away a piece still making it)
        self.jobs = []
        self.threads = []
        self.ends = {}  # block -> the frame the sound died away at in it
        self.took = []  # seconds the last pieces took, with their blocks: [(seconds, blocks)]
        self.error = None  # the first piece's error text (the caller says it)
        self.loaded = None  # (kestrel.Loaded, read in a piece's thread the first time)
        self._lock = threading.Lock()
        self._free = (0.0, None)  # (when free memory was last asked, the answer)

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

    # ------------------------------------------------------------ the song

    def set_song(self, song):
        """A new song: every block made again (R5 makes only the changed stretch)."""
        self.song = song
        self.clear()
        self.span = (song.first, song.last + int(TAIL * RATE)) if song is not None and len(song) else (0, 0)

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

    def _set_end(self):
        """The sound ends where a piece found the ring died away, else TAIL after the last note."""
        self.span = (self.span[0], int(min([self.song.last + int(TAIL * RATE)] + list(self.ends.values()))))

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
            for i in list(self.blocks):  # (behind the play line past BEHIND, or past the window: thrown away)
                if i not in keep and not (anchor - BEHIND * RATE <= (i + 1) * BLOCK and i * BLOCK <= anchor):
                    del self.blocks[i]
                    changed = True
            for job in self.jobs:
                if not any(i in keep for i in range(job.first, job.first + job.count)):
                    job.cancel.set()
            self.jobs = [j for j in self.jobs if not j.cancel.is_set()]
        self.threads = [t for t in self.threads if t.is_alive()]
        if self.song is None or self.error:
            return changed
        most = GPU_WORKERS if self.engine in ("merlin", "kestrel") else CPU_WORKERS
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
            job = _Job(i, len(run), [self.ver.get(j, 0) for j in run])
            with self._lock:
                self.jobs.append(job)
            t = threading.Thread(target=self._work, args=(job, self.song), daemon=True)
            t.start()
            self.threads.append(t)
            k += len(run)
            changed = True
        return changed

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
                    if len(part) < BLOCK and (i + 1) * BLOCK < self.span[1]:  # (the sound died away in it)
                        part = np.concatenate([part, np.zeros((BLOCK - len(part), 2), np.float32)])
                    # (its own copy: a block kept must not hold the whole piece's buffer)
                    self.blocks[i] = np.ascontiguousarray(part).copy() if job.count > 1 else part
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
        job.finished = True

    def _bass(self, job, song, f0, f1):
        stats = {}
        out = self.synth.render(song.bass_events(), song.ppq, f0, f1 - f0, self.opts["bass_voices"], False,
                                job.cancel, lambda n: setattr(job, "progress", n / max(1, f1 - f0)), stats)
        if out is not None and stats.get("end") is not None:
            job.end = int(stats["end"])
            out = out[:max(0, job.end - f0)]
        return out

    def _card(self, job, song, f0, f1):
        from files import kestrel
        data, skip = song.midi(f0, f1)
        if data is None:  # (nothing sounds here: silence, no piece)
            return np.zeros((f1 - f0, 2), np.float32)
        if self.loaded is None or not self.loaded.handle:
            self.loaded = kestrel.loaded_for(self.fonts, self.card)
        if job.cancel.is_set():
            return None
        fd, path = tempfile.mkstemp(".mid", "spiderweb-piece-")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        opts = {"block": self.opts["block"]}
        if self.engine == "kestrel":
            opts.update(voices=self.opts["voices"], interpolation=self.opts["interpolation"],
                        min_velocity=self.opts["min_velocity"])
        piece = kestrel.Piece(self.loaded, path, self.engine, skip, f1 - f0, delete_midi=True, **opts)
        try:
            while True:
                state = piece.wait(0.05)
                if job.cancel.is_set():
                    piece.cancel()
                    return None
                if state != kestrel.RUNNING:
                    break
                job.progress = piece.status()["progress"]
            if state == kestrel.FAILED:
                from files.synth import SynthError
                raise SynthError("playback.kestrel_failed", err=piece.error() or "?")
            if state != kestrel.DONE:
                return None
            out = piece.audio()
        finally:
            piece.close()
        if out is not None and len(out) < f1 - f0 and f0 + len(out) >= song.last:  # (the ring died away)
            job.end = f0 + len(out)
        elif out is not None and len(out) < f1 - f0:  # (silence at the piece's end left out: put back)
            out = np.concatenate([out, np.zeros((f1 - f0 - len(out), 2), np.float32)])
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
        if start >= len(got):  # (past where the sound died away)
            return np.zeros((0, 2), np.float32)
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
        """Seconds of sound made in a row from frame at (the R1c lowering looks at it)."""
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
