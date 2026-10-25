"""Reading any MIDI file safely (Load MIDI): its layout, a check of every track and its fingerprint. Never holds the
file whole in memory nor keeps it open or mapped: tracks go through one reused buffer, piece by piece, so a 17 GB
file reads like a small one. The file's size + time are checked before every piece; a file changed meanwhile =
FileChanged (nothing read from it is trusted any more).

A MIDI file here = its pieces in order: "raw" bytes (the header, unknown chunks, anything between or after) kept
exactly as they are, and "track" chunks (MTrk). A RIFF MIDI (.rmi) is read through its "data" chunk."""

import hashlib
import os
import struct
import threading

import numpy as np

from files import midi_scan, speed
from files.midi_scan import (AFTER_EOT, BAD, C_SIZE, CUT, NO_EOT, ST_AFTER, ST_EOT, ST_FLAGS, ST_GOOD, ST_SIZE,
                             ST_TICK)

PIECE = 16 << 20  # bytes read at a time
BIGGEST_EVENT = 1 << 30  # one meta / sysex event bigger than this = the track can't be read
SCAN_WORKERS = 8  # tracks read side by side
JOB_BYTES = 4 << 20  # small tracks read together in jobs of about this many bytes
JOB_TRACK = 4096  # what one track costs on top of its bytes (when grouping)
# file-level marks (MidiFile.marks)
M_RMID = "rmid"  # a RIFF MIDI (.rmi)
M_SMPTE = "smpte"  # time in frames, not ticks per beat
M_FORMAT = "format"  # format not 0 / 1 (2: separate songs)
M_COUNT = "count"  # the header's track count differs from the tracks found
M_CUT = "cut"  # the last chunk runs past the file's end
M_JUNK = "junk"  # bytes that aren't a chunk (kept as they are)
M_LONG_HEAD = "long_head"  # a header longer than 6 bytes
M_RESYNC = "resync"  # a track's length was wrong: the next track found by its "MTrk" (MidiFile.resynced)


class MidiError(Exception):
    """Not a MIDI file / can't be read. key = the message's text key (lang), info = its places."""

    def __init__(self, key, **info):
        super().__init__(key)
        self.key, self.info = key, info


class FileChanged(MidiError):
    def __init__(self):
        super().__init__("midi.changed")


class Cancelled(Exception):
    pass


class Track:
    """One MTrk chunk: where its bytes are, what reading it found."""
    __slots__ = ("number", "head", "start", "size", "declared", "flags", "counts", "last_tick", "after", "hash")

    def __init__(self, number, head, start, size, declared):
        self.number = number
        self.head = head  # (where its "MTrk" is)
        self.start, self.size = start, size  # (its events: size = what's really there)
        self.declared = declared  # (the length its chunk says: more than size = the file ends inside it)
        self.flags = 0
        self.counts = None  # (midi_scan.C_*), after scan()
        self.last_tick = 0
        self.after = 0  # bytes after End of Track
        self.hash = None  # blake2b of its events' bytes

    @property
    def whole(self):
        """Read to its end with nothing missing (only such a track is ever written again event by event)."""
        return not self.flags & (BAD | CUT) and self.size == self.declared


class MidiFile:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.marks = set()
        self.tracks = []
        self.resynced = []  # (numbers of the tracks whose length was wrong: M_RESYNC)
        self.pieces = []  # [("raw", start, size) / ("track", Track)] in file order
        self.scanned = False
        self.raw_hash = None  # (scan(): one hash of the header, track heads and other bytes)
        try:
            stat = os.stat(self.path)
        except OSError as e:
            raise MidiError("midi.cant_open", why=e.strerror or str(e)) from None
        self.size, self.mtime = stat.st_size, stat.st_mtime_ns
        try:
            with open(self.path, "rb") as f:
                self._layout(f)
        except OSError as e:
            raise MidiError("midi.cant_open", why=e.strerror or str(e)) from None

    # ------------------------------------------------------------------ layout

    def _layout(self, f):
        first = f.read(12)
        base, top = 0, self.size
        if first[:4] == b"RIFF" and first[8:12] == b"RMID":
            base, top = self._rmid(f)
            self.marks.add(M_RMID)
        f.seek(base)
        head = f.read(14)
        if len(head) < 14 or head[:4] != b"MThd":
            raise MidiError("midi.not_midi")
        length = struct.unpack(">I", head[4:8])[0]
        if length < 6:
            raise MidiError("midi.not_midi")
        if length > 6:
            self.marks.add(M_LONG_HEAD)
        self.format, self.declared, division = struct.unpack(">HHH", head[8:14])
        if division & 0x8000:
            self.marks.add(M_SMPTE)
            self.ppq = None
        else:
            self.ppq = division
            if division == 0:
                raise MidiError("midi.not_midi")
        if self.format > 1:
            self.marks.add(M_FORMAT)
        pos = base + 8 + length
        raw_from = 0  # (everything up to here goes out as it is)
        while pos + 8 <= top:
            f.seek(pos)
            h = f.read(8)
            if len(h) < 8:  # (the file got shorter while it was read)
                raise FileChanged()
            kind, size = h[:4], struct.unpack(">I", h[4:8])[0]
            if not all(65 <= c <= 90 or 97 <= c <= 122 or 48 <= c <= 57 or c == 32 for c in kind):
                if self._resync(f, top):  # (the track before had a wrong length: on from the next "MTrk")
                    t = self.tracks[-1]
                    raw_from, pos = t.start + t.size, t.start + t.size
                    continue
                self.marks.add(M_JUNK)  # (no chunk here: the rest is kept as it is)
                break
            if kind == b"MTrk":
                if pos > raw_from:
                    self.pieces.append(("raw", raw_from, pos - raw_from))
                have = min(size, top - pos - 8)
                t = Track(len(self.tracks), pos, pos + 8, have, size)
                self.tracks.append(t)
                self.pieces.append(("track", t))
                raw_from = pos + 8 + have
                if have < size and self._resync(f, top):
                    t = self.tracks[-1]
                    raw_from, pos = t.start + t.size, t.start + t.size
                    continue
                if have < size:
                    self.marks.add(M_CUT)
            pos += 8 + size
            if pos > top:
                break
        if pos < top and pos + 8 > top:
            self.marks.add(M_JUNK)
        if raw_from < self.size:
            self.pieces.append(("raw", raw_from, self.size - raw_from))
        if len(self.tracks) != self.declared:
            self.marks.add(M_COUNT)
        if not self.tracks:
            raise MidiError("midi.no_tracks")

    def _resync(self, f, top):
        """No chunk where the last track's length says the next one starts, or that length runs past the file's
        end: the length is wrong. The next "MTrk" after the track's own start is taken as the next track (user
        2026-10-23: find it and warn); the track before keeps the bytes up to it (only ever copied, its length as
        the file has it). True = found."""
        if not self.tracks:
            return False
        t = self.tracks[-1]
        at = t.start
        tail = b""
        while at < top:
            f.seek(at)
            block = f.read(min(PIECE, top - at))
            if not block:
                raise FileChanged()
            hay = tail + block
            i = hay.find(b"MTrk")
            while i >= 0:
                x = at - len(tail) + i
                if x + 8 <= top:
                    size = struct.unpack(">I", hay[i + 4:i + 8])[0] if i + 8 <= len(hay) else None
                    if size is None:
                        f.seek(x + 4)
                        size = struct.unpack(">I", f.read(4))[0]
                    t.size = x - t.start
                    self.marks.add(M_RESYNC)
                    self.resynced.append(t.number)
                    have = min(size, top - x - 8)
                    n = Track(len(self.tracks), x, x + 8, have, size)
                    self.tracks.append(n)
                    self.pieces.append(("track", n))
                    if have < size and not self._resync(f, top):
                        self.marks.add(M_CUT)
                    return True
                i = hay.find(b"MTrk", i + 1)
            tail = hay[-3:]
            at += len(block)
        return False

    def _rmid(self, f):
        """The SMF inside a RIFF MIDI's "data" chunk: (start, end)."""
        pos = 12
        while pos + 8 <= self.size:
            f.seek(pos)
            h = f.read(8)
            if len(h) < 8:
                raise FileChanged()
            size = struct.unpack("<I", h[4:8])[0]
            if h[:4] == b"data":
                return pos + 8, min(self.size, pos + 8 + size)
            pos += 8 + size + (size & 1)
        raise MidiError("midi.not_midi")

    # ------------------------------------------------------------------ reading

    def same(self):
        """Raises FileChanged when the file isn't the one read (size or time)."""
        try:
            stat = os.stat(self.path)
        except OSError:
            raise FileChanged() from None
        if stat.st_size != self.size or stat.st_mtime_ns != self.mtime:
            raise FileChanged()

    def watch(self):
        """A checker for one job of reading: the file's size + time asked at its first read and then once every
        PIECE bytes read (not at every small track: tens of thousands of them made reading slow)."""
        return _Watch(self)

    def pieces_of(self, f, start, size, buf, watch=None):
        """(bytes read, final) one PIECE (or buf's size) at a time from start, size bytes in all, into buf's
        front after what's left there (yields how much is filled; the caller says how much it used via send)."""
        done, keep = 0, 0
        watch = watch or self.watch()
        while True:
            want = min(len(buf) - keep, size - done)
            watch.read(want)
            f.seek(start + done)
            got = f.readinto(memoryview(buf)[keep:keep + want]) if want else 0
            if got != want:
                raise FileChanged()
            done += got
            used = yield keep + got, done >= size
            keep = keep + got - used
            if keep:
                buf[:keep] = buf[used:used + keep]
            if done >= size and not keep:
                return

    def scan(self, progress=None, cancel=None, workers=None, each=None):
        """Reads every track: its events checked + counted (Track.flags / counts / last_tick), its hash. Tracks are
        read side by side on several cores (biggest first) when the compiled loops are there. progress(bytes done,
        bytes in all) after each piece and cancel() (True = Cancelled) are called FROM THOSE THREADS.
        each(track) = something more done in the same read (Load MIDI's pass): an object whose .piece(bytes, used,
        at) gets every piece's checked whole events (bytes[:used], the track's byte at the piece's start) and
        .done() the end (also called on those threads)."""
        loops = speed.loops()
        walk = loops.midi_walk if loops else midi_scan.walk
        total = sum(t.size for t in self.tracks)
        lock, done, stop = threading.Lock(), [0], threading.Event()

        def step(n):
            if stop.is_set() or (cancel and cancel()):
                stop.set()
                raise Cancelled()
            if n:
                with lock:
                    done[0] += n
                    now = done[0]
                if progress:
                    progress(now, total)

        def one(job):
            watch = self.watch()
            with open(self.path, "rb") as f:
                for t in job:
                    step(0)  # (Cancel / another job's error: no track started after it)
                    self._scan_track(f, t, walk, bool(loops), step, watch, each and each(t))

        jobs = scan_jobs(self.tracks)
        n = 1 if not loops else min(workers or SCAN_WORKERS, os.cpu_count() or 1, len(jobs))
        if n <= 1:
            for job in jobs:
                one(job)
        else:
            _side_by_side(jobs, one, n, stop)
        self.raw_hash = self._hash_raw()
        self.same()
        self.scanned = True
        return self

    def _scan_track(self, f, t, walk, compiled, step, watch, more=None):
        st = np.zeros(ST_SIZE, np.int64)
        cnt = np.zeros(C_SIZE, np.int64)
        h = hashlib.blake2b(digest_size=16)
        buf = bytearray(max(1, min(PIECE, t.size)))
        reader = self.pieces_of(f, t.start, t.size, buf, watch)
        filled, final = next(reader) if t.size else (0, True)
        hashed, taken = 0, 0  # (bytes of buf already in the hash; bytes of the track read so far)
        while True:
            view = np.frombuffer(buf, np.uint8, filled) if compiled else memoryview(buf)[:filled]
            with memoryview(buf) as m:
                h.update(m[hashed:filled])
            used = walk(view, 0, filled, final, t.size - taken - filled, st, cnt)
            if more is not None and min(used, st[ST_GOOD]) > 0:  # (a damaged track: its good part only)
                more.piece(view, int(min(used, st[ST_GOOD])), taken)
            del view  # (buf may grow next)
            if used == 0 and not final and not st[ST_FLAGS] & BAD:  # (an event bigger than the buffer: it ends
                grow = min(len(buf), t.size - taken - len(buf))  # inside the track, so it fits in what's left)
                if len(buf) >= BIGGEST_EVENT or grow <= 0:
                    st[ST_FLAGS] |= BAD
                else:
                    buf.extend(bytes(grow))
            if st[ST_FLAGS] & (BAD | CUT) and not final:  # (unreadable from here: the rest is only hashed)
                reader.close()
                step(t.size - taken)
                h = self._hash_track(f, t, buf)
                break
            taken += used
            step(used)
            hashed = filled - used
            if not t.size:
                break
            try:
                filled, final = reader.send(used)
            except StopIteration:
                break
        if not st[ST_EOT]:
            st[ST_FLAGS] |= NO_EOT
        t.flags = int(st[ST_FLAGS])
        t.counts = cnt
        t.last_tick = int(st[ST_TICK])
        t.after = int(st[ST_AFTER])
        t.hash = h.hexdigest()
        if more is not None:
            more.done()

    def _hash_track(self, f, t, buf):
        """The whole track's hash, read again through buf (a damaged track: its reading stopped part way)."""
        h = hashlib.blake2b(digest_size=16)
        done = 0
        with memoryview(buf) as m:
            while done < t.size:
                self.same()
                f.seek(t.start + done)
                got = f.readinto(m[:min(len(buf), t.size - done)])
                if not got:
                    raise FileChanged()
                h.update(m[:got])
                done += got
        return h

    def _hash_raw(self):
        """One hash of every byte that isn't a track's events (header, track heads, other chunks, junk)."""
        h = hashlib.blake2b(digest_size=16)
        watch = self.watch()
        with open(self.path, "rb") as f:
            for kind, *what in self.pieces:
                start, size = (what[0].head, 8) if kind == "track" else what
                done = 0
                while done < size:
                    watch.read(min(PIECE, size - done))
                    f.seek(start + done)
                    piece = f.read(min(PIECE, size - done))
                    if not piece:
                        raise FileChanged()
                    h.update(piece)
                    done += len(piece)
        return h.hexdigest()

    # ------------------------------------------------------------------ summary

    def fingerprint(self):
        """What a project keeps to know the file again: path, size, time, each track's hash + one hash of all the
        rest (header etc.)."""
        return {"path": self.path, "size": self.size, "mtime": self.mtime,
                "tracks": [t.hash for t in self.tracks], "rest": self.raw_hash}

    def counts(self):
        """All tracks' counts added (midi_scan.C_*; channels = the channels used, programs / drum kits used: as bits;
        the per-channel states left 0)."""
        out = np.zeros(C_SIZE, np.int64)
        bits = [midi_scan.C_CHANNELS, midi_scan.C_PROGS, midi_scan.C_PROGS + 1, midi_scan.C_KITS, midi_scan.C_KITS + 1]
        for t in self.tracks:
            if t.counts is not None:
                out[:midi_scan.C_ADDED] += t.counts[:midi_scan.C_ADDED]
                out[bits] |= t.counts[bits]
        return out

    def programs(self):
        """(programs, drum kits) the file's program changes use, as sets (program 0 / kit 0 not counted unless
        changed to)."""
        c = self.counts()

        def of(at):
            return {k for k in range(128) if int(c[at + (k >> 6)]) >> (k & 63) & 1}
        return of(midi_scan.C_PROGS), of(midi_scan.C_KITS)

    def damaged(self):
        """Tracks that can't be read to their end (BAD / CUT)."""
        return [t for t in self.tracks if t.flags & (BAD | CUT)]


class _Watch:
    def __init__(self, mf):
        self.mf, self.since = mf, PIECE

    def read(self, n):
        self.since += n
        if self.since >= PIECE:
            self.since = 0
            self.mf.same()


def scan_jobs(tracks):
    """Tracks in jobs for side-by-side reading: each big one alone (biggest first), small ones together up to
    JOB_BYTES a job (one open file and one size check per job, not per track)."""
    jobs, small, size = [], [], 0
    for t in sorted(tracks, key=lambda t: -t.size):
        if t.size >= JOB_BYTES:
            jobs.append([t])
            continue
        small.append(t)
        size += t.size + JOB_TRACK
        if size >= JOB_BYTES:
            jobs.append(small)
            small, size = [], 0
    if small:
        jobs.append(small)
    return jobs


def _side_by_side(jobs, one, n, stop):
    """one(job) for every job on n DAEMON threads (closing Spiderweb never waits for them). The first error stops
    the rest (each stops at its next piece) and is raised here once the running ones have stopped."""
    lock, errors, left = threading.Lock(), [], list(reversed(jobs))

    def work():
        while True:
            with lock:
                if not left or stop.is_set():
                    return
                job = left.pop()
            try:
                one(job)
            except BaseException as e:
                stop.set()
                with lock:
                    errors.append(e)
                return

    threads = [threading.Thread(target=work, daemon=True, name="midi scan") for _ in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    if errors:
        cancelled = [e for e in errors if not isinstance(e, Cancelled)]
        raise (cancelled or errors)[0]


def flag_names(flags):
    names = {BAD: "bad", CUT: "cut", NO_EOT: "no_eot", midi_scan.LONG_GAP: "long_gap",
             midi_scan.RUN_AFTER_META: "run_after_meta", AFTER_EOT: "after_eot", midi_scan.ODD_TEMPO: "odd_tempo",
             midi_scan.HIGH_DATA: "keys_256"}
    return [n for bit, n in names.items() if flags & bit]
