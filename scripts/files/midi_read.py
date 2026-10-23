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
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from files import midi_scan, speed
from files.midi_scan import (AFTER_EOT, BAD, C_SIZE, CUT, NO_EOT, ST_AFTER, ST_EOT, ST_FLAGS, ST_SIZE, ST_TICK)

PIECE = 16 << 20  # bytes read at a time
BIGGEST_EVENT = 1 << 30  # one meta / sysex event bigger than this = the track can't be read
SCAN_WORKERS = 8  # tracks read side by side
# file-level marks (MidiFile.marks)
M_RMID = "rmid"  # a RIFF MIDI (.rmi)
M_SMPTE = "smpte"  # time in frames, not ticks per beat
M_FORMAT = "format"  # format not 0 / 1 (2: separate songs)
M_COUNT = "count"  # the header's track count differs from the tracks found
M_CUT = "cut"  # the last chunk runs past the file's end
M_JUNK = "junk"  # bytes that aren't a chunk (kept as they are)
M_LONG_HEAD = "long_head"  # a header longer than 6 bytes


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
        self.pieces = []  # [("raw", start, size) / ("track", Track)] in file order
        self.scanned = False
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
            kind, size = h[:4], struct.unpack(">I", h[4:8])[0]
            if not all(65 <= c <= 90 or 97 <= c <= 122 or 48 <= c <= 57 or c == 32 for c in kind):
                self.marks.add(M_JUNK)  # (no chunk here: the rest is kept as it is)
                break
            if kind == b"MTrk":
                if pos > raw_from:
                    self.pieces.append(("raw", raw_from, pos - raw_from))
                have = min(size, top - pos - 8)
                if have < size:
                    self.marks.add(M_CUT)
                t = Track(len(self.tracks), pos, pos + 8, have, size)
                self.tracks.append(t)
                self.pieces.append(("track", t))
                raw_from = pos + 8 + have
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

    def _rmid(self, f):
        """The SMF inside a RIFF MIDI's "data" chunk: (start, end)."""
        pos = 12
        while pos + 8 <= self.size:
            f.seek(pos)
            h = f.read(8)
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

    def pieces_of(self, f, start, size, buf):
        """(bytes read, final) one PIECE (or buf's size) at a time from start, size bytes in all, into buf's
        front after what's left there (yields how much is filled; the caller says how much it used via send)."""
        done, keep = 0, 0
        while True:
            self.same()
            want = min(len(buf) - keep, size - done)
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

    def scan(self, progress=None, cancel=None, workers=None):
        """Reads every track: its events checked + counted (Track.flags / counts / last_tick), its hash. Tracks are
        read side by side on several cores (biggest first) when the compiled loops are there. progress(bytes done,
        bytes in all) after each piece and cancel() (True = Cancelled) are called FROM THOSE THREADS."""
        loops = speed.loops()
        walk = loops.midi_walk if loops else midi_scan.walk
        total = sum(t.size for t in self.tracks)
        lock, done, stop = threading.Lock(), [0], threading.Event()

        def step(n):
            if stop.is_set() or (cancel and cancel()):
                stop.set()
                raise Cancelled()
            with lock:
                done[0] += n
                now = done[0]
            if progress:
                progress(now, total)

        def one(t):
            with open(self.path, "rb") as f:
                self._scan_track(f, t, walk, bool(loops), step)

        order = sorted(self.tracks, key=lambda t: -t.size)
        n = 1 if not loops else min(workers or SCAN_WORKERS, os.cpu_count() or 1, len(order))
        if n <= 1:
            for t in order:
                one(t)
        else:
            with ThreadPoolExecutor(n, thread_name_prefix="midi scan") as pool:
                jobs = [pool.submit(one, t) for t in order]
                try:
                    for job in jobs:
                        job.result()
                except BaseException:
                    stop.set()  # (the others stop at their next piece)
                    raise
        self.same()
        self.scanned = True
        return self

    def _scan_track(self, f, t, walk, compiled, step):
        st = np.zeros(ST_SIZE, np.int64)
        cnt = np.zeros(C_SIZE, np.int64)
        h = hashlib.blake2b(digest_size=16)
        buf = bytearray(max(1, min(PIECE, t.size)))
        reader = self.pieces_of(f, t.start, t.size, buf)
        filled, final = next(reader) if t.size else (0, True)
        hashed, taken = 0, 0  # (bytes of buf already in the hash; bytes of the track read so far)
        while True:
            view = np.frombuffer(buf, np.uint8, filled) if compiled else memoryview(buf)[:filled]
            with memoryview(buf) as m:
                h.update(m[hashed:filled])
            used = walk(view, 0, filled, final, st, cnt)
            del view  # (buf may grow next)
            if used == 0 and not final and not st[ST_FLAGS] & BAD:  # (an event bigger than the buffer)
                if len(buf) >= BIGGEST_EVENT:
                    st[ST_FLAGS] |= BAD
                else:
                    buf.extend(bytes(len(buf)))
            if st[ST_FLAGS] & BAD:  # (unreadable from here: the rest is only hashed)
                reader.close()
                step(t.size - taken)
                h = self._hash_track(f, t)
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

    def _hash_track(self, f, t):
        h = hashlib.blake2b(digest_size=16)
        done = 0
        while done < t.size:
            self.same()
            f.seek(t.start + done)
            piece = f.read(min(PIECE, t.size - done))
            if not piece:
                raise FileChanged()
            h.update(piece)
            done += len(piece)
        return h

    # ------------------------------------------------------------------ summary

    def fingerprint(self):
        """What a project keeps to know the file again: path, size, time, each track's hash."""
        return {"path": self.path, "size": self.size, "mtime": self.mtime,
                "tracks": [t.hash for t in self.tracks]}

    def counts(self):
        """All tracks' counts added (midi_scan.C_*; channels = the channels used, as bits)."""
        out = np.zeros(C_SIZE, np.int64)
        channels = 0
        for t in self.tracks:
            if t.counts is not None:
                out += t.counts
                channels |= int(t.counts[midi_scan.C_CHANNELS])
        out[midi_scan.C_CHANNELS] = channels
        return out

    def damaged(self):
        """Tracks that can't be read to their end (BAD / CUT)."""
        return [t for t in self.tracks if t.flags & (BAD | CUT)]


def flag_names(flags):
    names = {BAD: "bad", CUT: "cut", NO_EOT: "no_eot", midi_scan.LONG_GAP: "long_gap",
             midi_scan.RUN_AFTER_META: "run_after_meta", AFTER_EOT: "after_eot", midi_scan.ODD_TEMPO: "odd_tempo",
             midi_scan.HIGH_DATA: "keys_256"}
    return [n for bit, n in names.items() if flags & bit]
