"""A loaded MIDI file (Load MIDI, user 2026-10-23): its notes are never copied into Spiderweb's shapes. The file
stays the boss; Spiderweb keeps what the loading pass found (files/midi_index.py): per track an index to start
reading its notes anywhere, its key range, its own small picture, its tempos / names, and the overview picture of
the whole song. "Keep in memory" keeps the tracks' bytes too (read from there instead of the file).

Loader.run() reads the file once (every track checked + fingerprinted + indexed in the same read, on several
cores), then LoadedMidi holds the result."""

import os
import threading
import time

import numpy as np

from files import midi_index, speed
from files.midi_index import (CP_LEAST, GO, GRID_MOST, MORE_CP, MORE_ENT, MORE_GRID, MORE_KEYS, MORE_META, MORE_POOL, META_KEEP,
                              QS, X_END, X_FIRST, X_GHI, X_GLO, X_HANG, X_KHI, X_KLO, X_LONG, X_NCP, X_NENT, X_NMETA,
                              X_NOTES, X_ORPHAN, X_STOP, X_TICK, X_ZERO)
from files.midi_read import Cancelled, MidiError, MidiFile

PIC_COLS = 1024  # a track's own picture: this many columns at most across its notes (+ its key range)
PLAIN_MOST = 16 << 20  # bytes a file may have to load without the speed-ups (plain Python: ~1-2 MB a second)
SPARE = 2 << 30  # "Keep in memory" only with this much memory left over (user 2026-10-23)
TEMPO, NAME, PORT, TIME_SIG, KEY_SIG = 0x51, 0x03, 0x21, 0x58, 0x59


class TrackInfo:
    """What the pass found in one track."""
    __slots__ = ("number", "notes", "zero", "hang", "orphan", "long", "klo", "khi", "first", "end", "last_tick",
                 "chn", "channel", "cp_off", "cp_tick", "cp_run", "cp_ent", "e_q", "e_cnt", "e_end", "e_vel", "m_tick",
                 "m_type", "m_len", "m_data", "name", "pic", "pic_klo", "pic_glo", "pic_pf", "pic_cols", "data",
                 "limit", "stopped")

    def metas(self, kind):
        """[(tick, data bytes)] of this track's kept meta events of one kind (first META_KEEP bytes)."""
        at = np.flatnonzero(self.m_type == kind)
        return [(int(self.m_tick[i]), bytes(self.m_data[i, :min(int(self.m_len[i]), META_KEEP)])) for i in at]


class LoadedMidi:
    def __init__(self, mf, mode, G, infos, levels, seconds):
        self.mf, self.path, self.mode = mf, mf.path, mode
        self.ppq, self.G = mf.ppq, G
        self.tracks = infos  # [TrackInfo] in file order
        self.levels = levels  # [(cnt, top)]: the overview, level n = buckets of G << n ticks
        self.keys = levels[0][0].shape[0]  # 128, or 256 (a 256 keys MIDI)
        self.seconds = seconds
        self.notes = sum(t.notes for t in infos)
        self.end = max((t.end for t in infos if t.notes), default=0)  # the last note's end (ticks)
        self.last_tick = max((t.last_tick for t in infos), default=0)
        self.first = min((t.first for t in infos if t.notes), default=0)

    def counts(self):
        """What the summary says."""
        c = self.mf.counts()
        from files import midi_scan as S
        tempos = int(c[S.C_TEMPO])
        return {"notes": self.notes, "tracks": len(self.tracks),
                "with_notes": sum(1 for t in self.tracks if t.notes),
                "channels": bin(int(c[S.C_CHANNELS])).count("1"), "tempos": tempos,
                "zero": sum(t.zero for t in self.tracks), "hang": sum(t.hang for t in self.tracks),
                "orphan": sum(t.orphan for t in self.tracks), "long": sum(t.long for t in self.tracks),
                "damaged": [t.number for t in self.mf.damaged()], "resynced": list(self.mf.resynced),
                "keys_256": self.keys > 128, "seconds": self.seconds}

    def index_bytes(self):
        """Memory the index + overview take (the tracks' bytes not counted)."""
        n = sum(a.nbytes for c, t in self.levels for a in (c, t))
        for t in self.tracks:
            n += sum(getattr(t, k).nbytes for k in ("cp_off", "cp_tick", "cp_run", "cp_ent", "e_q", "e_cnt", "e_end",
                                                    "e_vel", "m_tick", "m_type", "m_len", "m_data", "pic"))
        return n

    def held_bytes(self):
        return sum(len(t.data) for t in self.tracks if t.data is not None)

    def channel_of(self, number):
        return self.tracks[number].channel

    def track_name(self, number):
        return self.tracks[number].name


def memory_need(mf):
    """Bytes "Keep in memory" takes on top of reading from the file (the tracks' bytes)."""
    return sum(t.size for t in mf.tracks)


def can_keep(mf, free):
    """"Keep in memory" is offered (free = bytes of memory free now, None = unknown)."""
    return free is not None and memory_need(mf) + SPARE <= free


class _Thread:
    """One reading thread's reused arrays (the per-track pairing ones are cleared for each track)."""

    def __init__(self, nk, ng):
        self.q_hd = np.full(QS, -1, np.int64)
        self.q_tl = np.full(QS, -1, np.int64)
        self.q_rl = np.zeros(QS, np.int64)
        self.q_pos = np.full(QS, -1, np.int64)
        self.q_vel = np.zeros(QS, np.int64)
        self.opened = np.zeros(QS, np.int64)
        self.pt = np.zeros(1 << 14, np.int64)
        self.pn = np.zeros(1 << 14, np.int64)
        self.pw = np.zeros(1 << 14, np.int64)
        self.cov = np.zeros((nk, ng), np.int32)
        self.chn = np.zeros(16, np.int64)

    def clear(self):
        self.q_hd.fill(-1)
        self.q_tl.fill(-1)
        self.q_rl.fill(0)
        self.q_pos.fill(-1)
        self.chn.fill(0)


class Loader:
    """Reads a MIDI file once into a LoadedMidi. run() on a background thread; the window reads .stage / .done /
    .total / .notes_found / .tracks_done while it runs, and sets .cancelled to stop it."""

    def __init__(self, mf, keep):
        self.mf, self.keep = mf, keep
        self.G = max(1, (mf.ppq or 960) // 16)
        self.stage = "wait"  # "wait" (speed-ups) / "read" / "overview" / "done"
        self.done = 0
        self.total = sum(t.size for t in mf.tracks)
        self.notes_found = 0
        self.tracks_done = 0
        self.cancelled = False
        self.result = None
        self.error = None
        self._lock = threading.Lock()
        self._local = threading.local()
        self._infos = [None] * len(mf.tracks)
        nk = 128
        self.cnt = np.zeros((nk, 1024), np.int32)
        self.top = np.full((nk, 1024), -1, np.int32)
        self.started = time.perf_counter()

    def run(self):
        try:
            self.result = self._run()
        except BaseException as e:  # (the window says it; Cancelled = nothing changes)
            self.error = e
        self.stage = "done"

    def _run(self):
        while speed.loops() is None and not speed.without_loops():
            if self.cancelled:
                raise Cancelled()
            time.sleep(0.05)
        if speed.loops() is None and self.total > PLAIN_MOST:
            raise MidiError("load.needs_speed")
        self.stage = "read"
        loops = speed.loops()
        self._index = loops.midi_index if loops else midi_index.index
        self._close = loops.midi_close if loops else midi_index.close
        self._grid = loops.midi_grid if loops else midi_index.grid
        self.mf.scan(progress=self._progress, cancel=lambda: self.cancelled, each=self._each)
        self.stage = "overview"
        if self.cancelled:
            raise Cancelled()
        levels = self._levels(loops)
        return LoadedMidi(self.mf, "memory" if self.keep else "file", self.G, self._infos, levels,
                          time.perf_counter() - self.started)

    def _progress(self, done, total):
        self.done = done

    def _each(self, t):
        th = getattr(self._local, "th", None)
        if th is None:
            th = self._local.th = _Thread(self.cnt.shape[0], 1024)
        return _TrackPass(self, t, th)

    def _levels(self, loops):
        halve = loops.midi_halve if loops else midi_index.halve
        with self._lock:
            ng = max(1, max((i.end for i in self._infos if i and i.notes), default=0) // self.G + 1)
            cnt, top = self.cnt[:, :ng].copy(), self.top[:, :ng].copy()
        self.cnt = self.top = None
        levels = [(cnt, top)]
        while cnt.shape[1] > 1:
            n2 = (cnt.shape[1] + 1) // 2
            c2, t2 = np.zeros((cnt.shape[0], n2), np.int32), np.zeros((cnt.shape[0], n2), np.int32)
            halve(cnt, top, c2, t2)
            levels.append((c2, t2))
            cnt, top = c2, t2
        return levels

    def grow_grid(self, nk, ng):
        """The overview grows to nk keys x ng buckets at least (under the lock)."""
        k0, g0 = self.cnt.shape
        if nk <= k0 and ng <= g0:
            return
        nk, ng = max(nk, k0), max(ng, g0 * 2)
        cnt = np.zeros((nk, ng), np.int32)
        top = np.full((nk, ng), -1, np.int32)
        cnt[:k0, :g0] = self.cnt
        top[:k0, :g0] = self.top
        self.cnt, self.top = cnt, top


class _TrackPass:
    """Load MIDI's pass over one track (MidiFile.scan's each=): pieces in, a TrackInfo out."""

    def __init__(self, loader, t, th):
        self.L, self.t, self.th = loader, t, th
        th.clear()
        self.ix = midi_index.new_state()
        self.cp_off = np.zeros(16, np.int64)
        self.cp_tick = np.zeros(16, np.int64)
        self.cp_run = np.zeros(16, np.int64)
        self.cp_ent = np.zeros(17, np.int64)
        self.e_q = np.zeros(256, np.int64)
        self.e_cnt = np.zeros(256, np.int64)
        self.e_end = np.zeros(256, np.int64)
        self.e_vel = np.zeros(256, np.int64)
        self.e_nx = np.zeros(256, np.int64)
        self.m_tick = np.zeros(8, np.int64)
        self.m_type = np.zeros(8, np.int64)
        self.m_len = np.zeros(8, np.int64)
        self.m_data = np.zeros((8, META_KEEP), np.uint8)
        self.data = np.empty(t.size, np.uint8) if loader.keep else None
        self.limit = 0

    def piece(self, view, used, at):
        if self.L.cancelled:
            raise Cancelled()
        if self.data is not None:
            self.data[at:at + used] = np.frombuffer(view, np.uint8, used) if isinstance(view, memoryview) else view[:used]
        th, ix = self.th, self.ix
        before = ix[X_NOTES]
        p = 0
        while not ix[X_STOP]:
            status, p = self.L._index(view, p, used, at, self.L.G, ix, th.q_hd, th.q_tl, th.q_rl, th.q_pos, th.q_vel, th.opened,
                                      th.pt, th.pn, th.pw, self.cp_off, self.cp_tick, self.cp_run, self.cp_ent,
                                      self.e_q, self.e_cnt, self.e_end, self.e_vel, self.e_nx, th.cov, th.chn, self.m_tick,
                                      self.m_type, self.m_len, self.m_data)
            if status == GO:
                break
            self.grow(status)
        self.limit = at + (p if ix[X_STOP] else used)
        with self.L._lock:
            self.L.notes_found += int(ix[X_NOTES] - before)

    def grow(self, status):
        th = self.th
        if status == MORE_POOL:
            n = len(th.pt) * 2
            th.pt, th.pn, th.pw = (np.concatenate([a, np.zeros(n - len(a), np.int64)]) for a in (th.pt, th.pn, th.pw))
        elif status == MORE_CP:
            n = len(self.cp_off) * 2
            self.cp_off, self.cp_tick, self.cp_run = (np.concatenate([a, np.zeros(n - len(a), np.int64)])
                                                      for a in (self.cp_off, self.cp_tick, self.cp_run))
            self.cp_ent = np.concatenate([self.cp_ent, np.zeros(n + 1 - len(self.cp_ent), np.int64)])
        elif status == MORE_ENT:
            n = max(len(self.e_q) * 2, int(self.ix[X_NENT]) + QS)
            self.e_q, self.e_cnt, self.e_end, self.e_vel, self.e_nx = (
                np.concatenate([a, np.zeros(n - len(a), np.int64)])
                for a in (self.e_q, self.e_cnt, self.e_end, self.e_vel, self.e_nx))
        elif status == MORE_META:
            n = len(self.m_tick) * 2
            self.m_tick, self.m_type, self.m_len = (np.concatenate([a, np.zeros(n - len(a), np.int64)])
                                                    for a in (self.m_tick, self.m_type, self.m_len))
            self.m_data = np.concatenate([self.m_data, np.zeros((n - len(self.m_data), META_KEEP), np.uint8)])
        elif status in (MORE_GRID, MORE_KEYS):
            self.cover(256 if status == MORE_KEYS else th.cov.shape[0], min(th.cov.shape[1] * 2, GRID_MOST))

    def cover(self, nk, ng):
        """This thread's diff grid grows to nk keys x ng buckets (its counts so far kept)."""
        th = self.th
        k0, g0 = th.cov.shape
        if nk <= k0 and ng <= g0:
            return
        cov = np.zeros((max(nk, k0), max(ng, g0)), np.int32)
        cov[:k0, :g0] = th.cov
        th.cov = cov

    def done(self):
        L, t, th, ix = self.L, self.t, self.th, self.ix
        self.cover(th.cov.shape[0], min(int(ix[X_TICK]) // L.G + 3, GRID_MOST))
        L._close(ix, L.G, th.q_hd, th.q_rl, th.opened, th.pt, th.pn, th.pw, self.e_end, self.e_nx, th.cov)
        i = TrackInfo()
        i.number = t.number
        for name, at in (("notes", X_NOTES), ("zero", X_ZERO), ("hang", X_HANG), ("orphan", X_ORPHAN),
                         ("long", X_LONG)):
            setattr(i, name, int(ix[at]))
        i.first, i.end = (int(ix[X_FIRST]), int(ix[X_END])) if i.notes else (0, 0)
        i.klo, i.khi = (int(ix[X_KLO]), int(ix[X_KHI])) if i.notes else (0, -1)
        i.last_tick = int(ix[X_TICK])
        i.chn = th.chn.copy()
        i.channel = int(np.argmax(i.chn)) if i.notes else None
        ncp, nent, nmeta = int(ix[X_NCP]), int(ix[X_NENT]), int(ix[X_NMETA])
        i.cp_off, i.cp_tick, i.cp_run = self.cp_off[:ncp].copy(), self.cp_tick[:ncp].copy(), self.cp_run[:ncp].copy()
        i.cp_ent = self.cp_ent[:ncp + 1].copy()
        if not ncp:
            i.cp_ent[:] = 0
        i.e_q = self.e_q[:nent].astype(np.int16)
        i.e_cnt = self.e_cnt[:nent].copy()
        i.e_end = self.e_end[:nent].copy()
        i.e_vel = self.e_vel[:nent].astype(np.uint8)
        i.m_tick, i.m_type, i.m_len = self.m_tick[:nmeta].copy(), self.m_type[:nmeta].copy(), self.m_len[:nmeta].copy()
        i.m_data = self.m_data[:nmeta].copy()
        names = i.metas(NAME)
        i.name = names[0][1].decode("latin-1").strip("\0 ") if names else ""
        i.data = self.data
        i.limit = self.limit
        i.stopped = bool(ix[X_STOP])
        glo, ghi = int(ix[X_GLO]), int(ix[X_GHI])
        if i.notes and glo <= ghi:  # (notes, not all past the overview's end)
            pf = 1
            while (ghi - glo + 1 + pf - 1) // pf > PIC_COLS:
                pf *= 2
            cols = (ghi - glo + pf) // pf
            pic = np.zeros((i.khi - i.klo + 1, cols), np.uint8)
            with L._lock:
                L.grow_grid(th.cov.shape[0], ghi + 1)
                L._grid(th.cov, glo, ghi, i.klo, i.khi, L.cnt, L.top, t.number, pic, pf)
            i.pic, i.pic_klo, i.pic_glo, i.pic_pf, i.pic_cols = np.packbits(pic, axis=1), i.klo, glo, pf, cols
        else:
            i.pic, i.pic_klo, i.pic_glo, i.pic_pf, i.pic_cols = np.zeros((0, 0), np.uint8), 0, 0, 1, 0
        L._infos[t.number] = i
        with L._lock:
            L.tracks_done += 1


def load(path, keep=False, progress=None):
    """Loads a MIDI file in one go (tests, scratch): a LoadedMidi."""
    mf = MidiFile(path)
    loader = Loader(mf, keep)
    loader.run()
    if loader.error:
        raise loader.error
    return loader.result
