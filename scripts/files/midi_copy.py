"""Writing a loaded MIDI file out again (Load MIDI's export). The file itself is the source: every byte that isn't
changed is copied as it is, read again from the file (never rebuilt from what Spiderweb holds), so nothing the
file had can go missing. Written through safefile (temp file, then put in place); checked WHILE writing: each
track's bytes against the hash taken when the file was loaded, so a file changed since, or a copy that came out
different, is never put in place."""

import hashlib
import os
import queue
import shutil
import threading

import numpy as np

from files import midi_scan, safefile, speed
from files.midi_read import BIGGEST_EVENT, PIECE, SCAN_WORKERS, Cancelled, FileChanged, MidiError

ROOM_SPARE = 64 << 20  # free disk space wanted on top of the file's size
HELD_TRACK = 64 << 20  # a track written event by event up to this size is made in memory (else counted first)
CHECKS_HELD = 256 << 20  # copied bytes waiting for their check at most (the copy waits for the checks past it)
AHEAD = 8  # pieces read ahead of the writing at most
CHECK_APART = 256 << 10  # tracks from this size checked on other cores (smaller ones on the reading thread)
SEND_LEAST = 1 << 20  # small pieces joined up to this before they go to the writer


def same_file(a, b):
    a, b = os.path.abspath(a), os.path.abspath(b)
    if os.path.normcase(a) == os.path.normcase(b):
        return True
    try:
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def copy_through(mf, dst, walk=False, progress=None, cancel=None, retime=None, added=None, hidden=None):
    """Writes mf (a scanned MidiFile) to dst unchanged. walk = every readable track written event by event (what a
    changed track goes through) instead of copied: the result must be the very same bytes. progress(bytes done,
    bytes in all); cancel() True = Cancelled (dst untouched). retime (Retime): a changed tempo's tracks written
    again (old tempo events out, the new list in, ticks moved by Keep timing); the rest copied as it is. added
    (Added): Spiderweb's own tracks put after the file's last track. hidden(track number) = its events left out
    (Extract: sorted byte places, midi_scan), written again without them."""
    if not mf.scanned:
        raise ValueError("scan() the file first")
    if same_file(mf.path, dst) or same_file(mf.path, dst + ".tmp"):  # (safefile's temp file: writing it would
        raise MidiError("midi.same_file")  # empty the source, then delete it)
    folder = os.path.dirname(os.path.abspath(dst))
    try:
        free = shutil.disk_usage(folder).free
    except OSError as e:
        raise MidiError("midi.cant_write", why=e.strerror or str(e)) from None
    need = mf.size + ROOM_SPARE + (added.size + sum(len(c) for cs in added.before.values() for c in cs)
                                   if added is not None else 0)
    if free < need:
        raise MidiError("midi.no_room", need=-(-need // 2 ** 20), free=free // 2 ** 20)
    work = _Work(mf, walk, progress, cancel, retime, added, hidden)
    try:
        safefile.write_bytes(dst, work.pieces())
    except OSError as e:
        raise MidiError("midi.cant_write", why=e.strerror or str(e)) from None
    return work.written


class _Work:
    def __init__(self, mf, walk, progress, cancel, retime=None, added=None, hidden=None):
        self.mf, self.walk, self.progress, self.cancel, self.retime = mf, walk, progress, cancel, retime
        self.added = added
        self.hidden = hidden or (lambda n: NO_SKIP)
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
        """The bytes to write, read AHEAD on a thread of their own (reading and writing side by side: the copy took
        read + write time one after the other)."""
        q, stop = queue.Queue(AHEAD), threading.Event()

        def send(item):
            while not stop.is_set():
                try:
                    q.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    pass
            return False

        def read():
            pieces, small, size = self._all(), [], 0
            try:
                for piece in pieces:
                    if len(piece) < SEND_LEAST:  # (tens of thousands of tiny tracks: one hand-over each was slow)
                        small.append(piece)
                        size += len(piece)
                        if size < SEND_LEAST:
                            continue
                        piece, small, size = b"".join(small), [], 0
                    elif small:
                        if not send(b"".join(small)):
                            return
                        small, size = [], 0
                    if not send(piece):
                        return
                if small and not send(b"".join(small)):
                    return
                send((None, None))
            except BaseException as e:
                send((None, e))
            finally:
                pieces.close()

        th = threading.Thread(target=read, daemon=True, name="midi copy")
        th.start()
        try:
            while True:
                piece = q.get()
                if isinstance(piece, tuple):
                    if piece[1] is not None:
                        raise piece[1]
                    return
                yield piece
        finally:
            stop.set()  # (the writer stopped early: the reading ends at its next piece)
            th.join()

    def _all(self):
        mf = self.mf
        self.watch = mf.watch()
        rest = hashlib.blake2b(digest_size=16)  # (everything but the tracks' events, as read: = mf.raw_hash)
        added = self.added
        last = max(i for i, (kind, *_) in enumerate(mf.pieces) if kind == "track")
        checks = _Checks(min(SCAN_WORKERS, os.cpu_count() or 1))
        try:
            yield from self._pieces(mf, rest, added, last, checks)
            bad = checks.finish()
        finally:
            checks.stop()
        if bad:
            raise MidiError("midi.check_failed", track=bad[0] + 1)
        if rest.hexdigest() != mf.raw_hash:  # (the header etc. changed since it was read)
            raise FileChanged()
        mf.same()

    def _pieces(self, mf, rest, added, last, checks):
        with open(mf.path, "rb") as f:
            for i, (kind, *what) in enumerate(mf.pieces):
                if i == last + 1 and added is not None:
                    yield from self.own(added.chunks)
                if kind == "raw":
                    start, size = what
                    for k, piece in enumerate(self.raw(f, start, size)):
                        rest.update(piece)
                        if added is not None and start == 0 and k == 0:  # (the header)
                            piece = added.header(piece, len(mf.tracks))
                        yield piece
                    continue
                t = what[0]
                if added is not None and t.number in added.before:
                    yield from self.own(added.before[t.number])
                head = self.read_exact(f, t.head, 8)
                rest.update(head)
                rt = self.retime
                changed = t.whole and ((rt is not None and (rt.bp is not None or t.number in rt.tracks))
                                       or len(self.hidden(t.number)) > 0)
                if (self.walk or changed) and t.whole:
                    # a changed track: its bytes READ are checked (it comes out different); else what's written
                    h = hashlib.blake2b(digest_size=16)
                    hin = h if changed else None
                    if t.size <= HELD_TRACK:  # (written into memory once: its length known)
                        pieces = list(self.rewritten(f, t, None if changed else h, hin))
                        yield b"MTrk" + sum(len(p) for p in pieces).to_bytes(4, "big")
                        self.written += 8
                        yield from pieces
                    else:  # (a big track: once to count its bytes, once to write them)
                        size = self.rewritten(f, t, None, None, count=True)
                        yield b"MTrk" + size.to_bytes(4, "big")
                        self.written += 8
                        yield from self.rewritten(f, t, None if changed else h, hin)
                    if changed and h.hexdigest() != t.hash:  # (read differently than when loaded)
                        raise FileChanged()
                    if h.hexdigest() != t.hash:
                        raise MidiError("midi.check_failed", track=t.number + 1)
                else:  # (a damaged track is only ever copied, its length as the file has it)
                    yield head
                    self.written += 8
                    if t.size < CHECK_APART:  # (handing a tiny track over costs more than its hash)
                        h = hashlib.blake2b(digest_size=16)
                        for piece in self.raw(f, t.start, t.size):
                            h.update(piece)
                            yield piece
                        if h.hexdigest() != t.hash:
                            raise MidiError("midi.check_failed", track=t.number + 1)
                        continue
                    job = checks.start(t)  # (checked on another core while the copy goes on)
                    for piece in self.raw(f, t.start, t.size):
                        checks.put(job, piece)
                        yield piece
                    checks.end(job)
                    if checks.bad:  # (stopped at the next track, like before)
                        return
            if last == len(mf.pieces) - 1 and added is not None:
                yield from self.own(added.chunks)

    def own(self, chunks):
        for piece in chunks:
            if self.cancel and self.cancel():
                raise Cancelled()
            self.written += len(piece)
            yield piece

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

    def rewritten(self, f, t, h, hin, count=False):
        """count: how many bytes t comes to (an int); else a generator of its bytes, each put into h too (when
        given), the bytes read into hin."""
        if count:
            return sum(len(p) for p in self._rewrite(f, t, False, None))
        return self._hashed(self._rewrite(f, t, True, hin), h)

    def _hashed(self, pieces, h):
        for p in pieces:
            if h is not None:
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

    def _rewrite(self, f, t, report, hin):
        loops = speed.loops()
        rewrite = loops.midi_rewrite if loops else midi_scan.rewrite
        rt = self.retime
        none = np.zeros(1, np.int64)
        bp_n, bp_f = rt.bp if rt is not None and rt.bp is not None else (none, none)
        use_map = rt is not None and rt.bp is not None
        drop = rt.drop if rt is not None else np.zeros(128, np.uint8)
        ins_t, ins_at, ins_b = (rt.inserts.get(t.number) if rt is not None else None) or NO_INSERTS
        skip = np.ascontiguousarray(self.hidden(t.number), np.int64)
        buf = self.buf
        out = self.out_for(min(len(buf), t.size))
        st = np.zeros(midi_scan.RW_SIZE, np.int64)
        reader = self.mf.pieces_of(f, t.start, t.size, buf, self.watch)
        filled, final = next(reader)
        taken = 0
        while True:
            if loops:
                view, oview = np.frombuffer(buf, np.uint8, filled), np.frombuffer(out, np.uint8)
            else:
                view, oview = memoryview(buf)[:filled], out
            used, o = rewrite(view, 0, filled, final, bp_n, bp_f, use_map, drop, ins_t, ins_at, ins_b, oview, 0, st,
                              taken, skip)
            del view, oview
            if hin is not None and used:
                hin.update(memoryview(buf)[:used])
            if o:
                yield bytes(out[:o])
            if report:
                self.step(used)
            taken += used
            if final and used >= filled:
                break
            if used == 0 and o == 0:  # (an event bigger than the buffer: it ends inside the track, a whole one)
                grow = min(len(buf), t.size - taken - len(buf))
                if final or len(buf) >= BIGGEST_EVENT or grow <= 0:
                    raise MidiError("midi.check_failed", track=t.number + 1)
                buf.extend(bytes(grow))
                out = self.out_for(len(buf))
            try:
                filled, final = reader.send(used)
            except StopIteration:
                break
        k = int(st[midi_scan.RW_INS])
        if k < len(ins_t):  # (a track without End of Track: what's left to put in goes at its end)
            done, tail = int(st[midi_scan.RW_DONE]), bytearray()
            for j in range(k, len(ins_t)):
                tail += vlq(max(0, int(ins_t[j]) - done)) + bytes(ins_b[ins_at[j]:ins_at[j + 1]])
                done = max(done, int(ins_t[j]))
            yield bytes(tail)


class _Checks:
    """Each copied track's hash worked out on DAEMON threads while the copy goes on (hashlib lets go of the GIL:
    one core did ~850 MB/s, most of a plain export's time). The very bytes written are hashed, in order, one
    thread per track; bad = numbers of the tracks that came out different."""

    def __init__(self, n):
        self.jobs, self.bad, self.held, self.open = queue.Queue(), [], 0, None
        self.cv = threading.Condition()
        self.threads = [threading.Thread(target=self._work, daemon=True, name="midi check") for _ in range(n)]
        for th in self.threads:
            th.start()

    def start(self, t):
        self.open = job = (t, queue.SimpleQueue())
        self.jobs.put(job)
        return job

    def put(self, job, piece):
        with self.cv:
            while self.held > CHECKS_HELD and not self.bad:
                self.cv.wait()
            self.held += len(piece)
        job[1].put(piece)

    def end(self, job):
        job[1].put(None)
        self.open = None

    def _work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            t, pieces = job
            h = hashlib.blake2b(digest_size=16)
            while True:
                piece = pieces.get()
                if piece is None:
                    break
                h.update(piece)
                with self.cv:
                    self.held -= len(piece)
                    self.cv.notify_all()
            if h.hexdigest() != t.hash:
                with self.cv:
                    self.bad.append(t.number)
                    self.cv.notify_all()

    def finish(self):
        """Waits for every check; the bad tracks' numbers, smallest first."""
        for _ in self.threads:
            self.jobs.put(None)
        for th in self.threads:
            th.join()
        return sorted(self.bad)

    def stop(self):
        """(Cancelled / failed: the threads end without waiting for them.) The track still being copied is ended."""
        if self.open is not None:
            self.end(self.open)
        for _ in self.threads:
            self.jobs.put(None)


def vlq(n):
    out = [n & 127]
    n >>= 7
    while n:
        out.insert(0, (n & 127) | 128)
        n >>= 7
    return bytes(out)


class Added:
    """Spiderweb's own tracks put after a loaded MIDI's last track (its shapes drawn on top, plan step 10):
    chunks = whole MTrk chunks (bytes, made as they're written), count = how many (before's too), size = about how
    many bytes; before = {track number: [whole MTrk chunks]} put just before that file track (tracks of yours
    placed among the file's, group D). The header then says format 1 (a format 0 file has one track) and the new
    track count."""

    def __init__(self, chunks, count, size=0, before=None):
        self.chunks, self.count, self.size = chunks, count, size
        self.before = before or {}

    def header(self, piece, tracks):
        b = bytearray(piece)
        b[8:12] = (1).to_bytes(2, "big") + (tracks + self.count).to_bytes(2, "big")
        return bytes(b)


NO_SKIP = np.zeros(0, np.int64)
NO_INSERTS = (np.zeros(0, np.int64), np.zeros(1, np.int64), np.zeros(0, np.uint8))


class Retime:
    """What a changed tempo does to a loaded MIDI's export: bp = (anchors, their new ticks) when Keep timing moves
    events (int64 arrays, midi_scan.rewrite's map; None = every event on its tick), drop[type] = 1 for the meta
    events left out everywhere (tempo, and time / key signatures when they changed), inserts = {track number:
    [(tick, event bytes)]} the new ones (ticks as written), tracks = the ones written again (every one with a map)."""

    def __init__(self, bp, drop_types, inserts, tracks):
        self.bp = None if bp is None else tuple(np.ascontiguousarray(a, np.int64) for a in bp)
        self.drop = np.zeros(128, np.uint8)
        for k in drop_types:
            self.drop[k] = 1
        self.inserts = {}
        for n, events in inserts.items():
            events = sorted(events, key=lambda e: e[0])
            at = np.zeros(len(events) + 1, np.int64)
            at[1:] = np.cumsum([len(e[1]) for e in events])
            self.inserts[n] = (np.array([e[0] for e in events], np.int64), at,
                               np.frombuffer(b"".join(e[1] for e in events), np.uint8).copy())
        self.tracks = set(tracks) | set(inserts)

    def add(self, extra):
        """More events put in (group D: notes of shapes drawn into the file's tracks): {track number: (ticks, starts,
        bytes)} like self.inserts' (midi_out.note_inserts), merged in time order with what's there (those first on
        a shared tick)."""
        for n, (t, at, b) in extra.items():
            if n in self.inserts:
                t0, at0, b0 = self.inserts[n]
                ticks = np.concatenate([t0, t])
                sizes = np.concatenate([np.diff(at0), np.diff(at)])
                parts = [b0[at0[k]:at0[k + 1]] for k in range(len(t0))] + [b[at[k]:at[k + 1]] for k in range(len(t))]
                order = np.argsort(ticks, kind="stable")
                t = ticks[order]
                at = np.zeros(len(order) + 1, np.int64)
                at[1:] = np.cumsum(sizes[order])
                b = np.concatenate([parts[k] for k in order]) if len(order) else np.zeros(0, np.uint8)
            self.inserts[n] = (np.ascontiguousarray(t, np.int64), np.ascontiguousarray(at, np.int64),
                               np.ascontiguousarray(b, np.uint8))
            self.tracks.add(n)
        return self
