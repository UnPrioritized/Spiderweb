"""Writing a loaded MIDI file out again (Load MIDI's export). The file itself is the source: every byte that isn't
changed is copied as it is, read again from the file (never rebuilt from what Spiderweb holds), so nothing the
file had can go missing. Written through safefile (temp file, then put in place); checked WHILE writing: each
track's bytes against the hash taken when the file was loaded, so a file changed since, or a copy that came out
different, is never put in place."""

import hashlib
import os
import shutil

import numpy as np

from files import midi_scan, safefile, speed
from files.midi_read import BIGGEST_EVENT, PIECE, Cancelled, FileChanged, MidiError

ROOM_SPARE = 64 << 20  # free disk space wanted on top of the file's size
HELD_TRACK = 64 << 20  # a track written event by event up to this size is made in memory (else counted first)


def same_file(a, b):
    a, b = os.path.abspath(a), os.path.abspath(b)
    if os.path.normcase(a) == os.path.normcase(b):
        return True
    try:
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def copy_through(mf, dst, walk=False, progress=None, cancel=None):
    """Writes mf (a scanned MidiFile) to dst unchanged. walk = every readable track written event by event (what a
    changed track goes through) instead of copied: the result must be the very same bytes. progress(bytes done,
    bytes in all); cancel() True = Cancelled (dst untouched)."""
    if not mf.scanned:
        raise ValueError("scan() the file first")
    if same_file(mf.path, dst) or same_file(mf.path, dst + ".tmp"):  # (safefile's temp file: writing it would
        raise MidiError("midi.same_file")  # empty the source, then delete it)
    folder = os.path.dirname(os.path.abspath(dst))
    try:
        free = shutil.disk_usage(folder).free
    except OSError as e:
        raise MidiError("midi.cant_write", why=e.strerror or str(e)) from None
    if free < mf.size + ROOM_SPARE:
        raise MidiError("midi.no_room", need=-(-(mf.size + ROOM_SPARE) // 2 ** 20), free=free // 2 ** 20)
    work = _Work(mf, walk, progress, cancel)
    try:
        safefile.write_bytes(dst, work.pieces())
    except OSError as e:
        raise MidiError("midi.cant_write", why=e.strerror or str(e)) from None
    return work.written


class _Work:
    def __init__(self, mf, walk, progress, cancel):
        self.mf, self.walk, self.progress, self.cancel = mf, walk, progress, cancel
        self.done, self.written = 0, 0
        self.buf = bytearray(PIECE)
        self.out = None

    def step(self, n):
        if self.cancel and self.cancel():
            raise Cancelled()
        self.done += n
        if self.progress:
            self.progress(self.done, self.mf.size)

    def pieces(self):
        mf = self.mf
        self.watch = mf.watch()
        rest = hashlib.blake2b(digest_size=16)  # (everything but the tracks' events, as read: = mf.raw_hash)
        with open(mf.path, "rb") as f:
            for kind, *what in mf.pieces:
                if kind == "raw":
                    start, size = what
                    for piece in self.raw(f, start, size):
                        rest.update(piece)
                        yield piece
                    continue
                t = what[0]
                head = self.read_exact(f, t.head, 8)
                rest.update(head)
                if self.walk and t.whole:
                    h = hashlib.blake2b(digest_size=16)
                    if t.size <= HELD_TRACK:  # (written into memory once: its length known)
                        pieces = list(self.rewritten(f, t, h))
                        yield b"MTrk" + sum(len(p) for p in pieces).to_bytes(4, "big")
                        self.written += 8
                        yield from pieces
                    else:  # (a big track: once to count its bytes, once to write them)
                        size = self.rewritten(f, t, None)
                        yield b"MTrk" + size.to_bytes(4, "big")
                        self.written += 8
                        yield from self.rewritten(f, t, h)
                else:  # (a damaged track is only ever copied, its length as the file has it)
                    yield head
                    self.written += 8
                    h = hashlib.blake2b(digest_size=16)
                    for piece in self.raw(f, t.start, t.size):
                        h.update(piece)
                        yield piece
                if h.hexdigest() != t.hash:
                    raise MidiError("midi.check_failed", track=t.number + 1)
        if rest.hexdigest() != mf.raw_hash:  # (the header etc. changed since it was read)
            raise FileChanged()
        mf.same()

    def read_exact(self, f, start, size):
        self.watch.read(size)
        f.seek(start)
        got = f.read(size)
        if len(got) != size:
            raise FileChanged()
        return got

    def raw(self, f, start, size):
        done = 0
        while done < size:
            n = min(PIECE, size - done)
            piece = self.read_exact(f, start + done, n)
            done += n
            self.written += n
            self.step(n)
            yield piece

    def rewritten(self, f, t, h):
        """h None: how many bytes t comes to (an int); else a generator of its bytes, each put into h too."""
        if h is None:
            return sum(len(p) for p in self._rewrite(f, t, report=False))
        return self._hashed(self._rewrite(f, t, report=True), h)

    def _hashed(self, pieces, h):
        for p in pieces:
            h.update(p)
            self.written += len(p)
            yield p

    def out_for(self, n):
        """The write buffer for n bytes read (kept: made anew only when bigger is needed). Any one event read fits:
        it grows by 10 at most (a 1-byte gap -> up to 10 bytes, + a status byte); a full buffer = written out and
        the rewrite goes on where it stopped."""
        need = n + 64
        if self.out is None or len(self.out) < need:
            self.out = bytearray(need)
        return self.out

    def _rewrite(self, f, t, report):
        loops = speed.loops()
        rewrite = loops.midi_rewrite if loops else midi_scan.rewrite
        buf = self.buf
        out = self.out_for(min(len(buf), t.size))
        st = np.zeros(midi_scan.RW_SIZE, np.int64)
        table = np.zeros(1, np.int64)
        reader = self.mf.pieces_of(f, t.start, t.size, buf, self.watch)
        filled, final = next(reader)
        taken = 0
        while True:
            if loops:
                view, oview = np.frombuffer(buf, np.uint8, filled), np.frombuffer(out, np.uint8)
            else:
                view, oview = memoryview(buf)[:filled], out
            used, o = rewrite(view, 0, filled, final, table, False, oview, 0, st)
            del view, oview
            if o:
                yield bytes(out[:o])
            if report:
                self.step(used)
            taken += used
            if final and used >= filled:
                return
            if used == 0 and o == 0:  # (an event bigger than the buffer: it ends inside the track, a whole one)
                grow = min(len(buf), t.size - taken - len(buf))
                if final or len(buf) >= BIGGEST_EVENT or grow <= 0:
                    raise MidiError("midi.check_failed", track=t.number + 1)
                buf.extend(bytes(grow))
                out = self.out_for(len(buf))
            try:
                filled, final = reader.send(used)
            except StopIteration:
                return
