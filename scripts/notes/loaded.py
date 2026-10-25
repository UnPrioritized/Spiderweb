"""A loaded MIDI file (Load MIDI, user 2026-10-23): its notes are never copied into Spiderweb's shapes. The file
stays the boss; Spiderweb keeps what the loading pass found (files/midi_index.py): per track an index to start
reading its notes anywhere, its key range, its own small picture, its tempos / names, and the overview picture of
the whole song. "Keep in memory" keeps the tracks' bytes too (read from there instead of the file).

Loader.run() reads the file once (every track checked + fingerprinted + indexed in the same read, on several
cores), then LoadedMidi holds the result."""

import hashlib
import json
import os
import threading
import time

import numpy as np

from files import midi_index, midi_scan, speed
from files.midi_index import (CP_LEAST, GO, GRID_MOST, MORE_CP, MORE_ENT, MORE_GRID, MORE_KEYS, MORE_META, MORE_POOL, META_KEEP,
                              QS, X_END, X_FIRST, X_GHI, X_GLO, X_HANG, X_KHI, X_KLO, X_LONG, X_NCP, X_NENT, X_NMETA,
                              X_NOTES, X_ORPHAN, X_STOP, X_TICK, X_ZERO)
from files.midi_read import PIECE, Cancelled, MidiError, MidiFile

PIC_COLS = 1024  # a track's own picture: this many columns at most across its notes (+ its key range)
PLAIN_MOST = 16 << 20  # bytes a file may have to load without the speed-ups (plain Python: ~1-2 MB a second)
MARK_MOST = 1 << 26  # Keep timing's note ticks are marked this many ticks at a time (a byte each)
SPARE = 2 << 30  # "Keep in memory" only with this much memory left over (user 2026-10-23)
TEMPO, NAME, PORT, TIME_SIG, KEY_SIG = 0x51, 0x03, 0x21, 0x58, 0x59
PAIRING = "fifo"  # how note-ons and note-offs are paired (first on, first off: Domino, user 2026-10-23); saved


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
        self._anchors = None  # (anchors(): the note ticks, read once when first needed)

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

    def anchors(self, cancel=None, progress=None):
        """(note ticks, other ticks): every tick a note starts / ends on, and the ticks of other channel events (CC,
        bend...) with no note there; sorted int64 arrays (Keep timing's anchors / loose ticks, tempo_file.keep_gaps).
        One read of every track (from memory when kept), remembered; a song longer than MARK_MOST ticks is read a
        window of ticks at a time (memory by the window, never by the song's length: hunt 2026-10-24, a song
        ending 537 M ticks on took 1.1 GB). cancel() True = Cancelled; progress(done, total) in bytes."""
        if self._anchors is not None:
            return self._anchors
        loops = speed.loops()
        marks = loops.midi_note_ticks if loops else midi_scan.note_ticks
        mf, done = self.mf, 0
        span = self.end + 1  # (past the last note's end nothing is an anchor: events there move along with it)
        size = min(span, MARK_MOST)
        windows = range(0, span, size)
        total = sum(t.size for t in mf.tracks if t.whole) * len(windows)
        mark = np.zeros(size, np.uint8)
        buf = bytearray(PIECE)
        notes, other = [], []
        with open(mf.path, "rb") as f:
            watch = mf.watch()
            for base in windows:
                mark.fill(0)
                for t, info in zip(mf.tracks, self.tracks):
                    if not t.whole:  # (damaged: tempo work is locked for the file)
                        continue
                    st = np.zeros(midi_scan.NT_SIZE, np.int64)
                    if info.data is not None:
                        marks(info.data, 0, len(info.data), True, mark, base, st)
                        done += t.size
                        continue
                    reader = mf.pieces_of(f, t.start, t.size, buf, watch)
                    filled, final = next(reader)
                    while True:
                        if cancel and cancel():
                            raise Cancelled()
                        view = np.frombuffer(buf, np.uint8, filled) if loops else memoryview(buf)[:filled]
                        used = marks(view, 0, filled, final, mark, base, st)
                        del view
                        done += used
                        if progress:
                            progress(done, total)
                        if final and used >= filled:
                            break
                        try:
                            filled, final = reader.send(used)
                        except StopIteration:
                            break
                notes.append(np.flatnonzero(mark & midi_scan.NOTE_TICK).astype(np.int64) + base)
                other.append(np.flatnonzero(mark == midi_scan.OTHER_TICK).astype(np.int64) + base)
        self._anchors = np.concatenate(notes), np.concatenate(other)
        return self._anchors

    def link(self):
        """What a project file keeps to find this MIDI again and know it's the same (never its notes)."""
        return {"path": self.path, "size": self.mf.size, "mtime": self.mf.mtime, "hash": file_hash(self.mf),
                "mode": self.mode, "ppq": self.ppq, "pairing": PAIRING}

    def channel_of(self, number):
        return self.tracks[number].channel

    def track_name(self, number):
        return self.tracks[number].name


def file_tempos(lm):
    """The file's own tempo as players read it: [(tick, us)], each a change (the same as the one before = none);
    several on one tick (a list copied into several tracks): the last in the file wins. Remembered (the pane asks
    at every redraw)."""
    if getattr(lm, "_file_tempos", None) is not None:
        return lm._file_tempos
    got = {}
    for t in lm.tracks:
        for tick, data in t.metas(TEMPO):
            if len(data) >= 3 and int.from_bytes(data[:3], "big"):
                got[tick] = int.from_bytes(data[:3], "big")
    out = []
    for tick, us in sorted(got.items()):
        if not out or out[-1][1] != us:
            out.append((tick, us))
    lm._file_tempos = out
    return out


def conductor_line(lm):
    """The file's tempos, time and key signatures as a Conductor pane line (notes/tempo.py; group C step 8): each
    tempo change a held point at its tick, its BPM exactly the file's (60,000,000 / us); a tempo the same as the one
    before it is no new point; several on one tick (a list copied into several tracks): the last in the file wins,
    as players read them. Before the first one: MIDI's 120."""
    from notes.meter import clean_keysigs, clean_sigs
    from notes.tempo import clean_tempo, empty
    ppq = lm.ppq or 480

    def every(kind):
        got = {}
        for t in lm.tracks:  # (file order: a later track's on the same tick wins)
            for tick, data in t.metas(kind):
                got[tick] = data
        return sorted(got.items())
    line = empty()
    for tick, us in file_tempos(lm):
        # (the exact beat, never rounded: the file's tick is the first one at or after it, tempo.beat_tick; rounded,
        # a tick off a round beat came out a tick late, hunt 2026-10-24)
        line["points"].append([tick / ppq, 60_000_000 / us, "hold", "", "lin", False, [0.5, 0.5]])
    line["sigs"] = clean_sigs([[round(tick / ppq, 9), d[0], 2 ** d[1]] for tick, d in every(TIME_SIG)
                               if len(d) >= 2 and d[1] <= 7])
    line["keysigs"] = clean_keysigs([[round(tick / ppq, 9), int.from_bytes(d[:1], "big", signed=True), d[1] == 1]
                                     for tick, d in every(KEY_SIG) if len(d) >= 2])
    return clean_tempo(line)


CONDUCTOR_KEYS = ("points", "catch", "sigs", "keysigs")  # (what a Conductor line changes in the file)


def same_conductor(tempo, line, keys=CONDUCTOR_KEYS):
    """The Conductor pane's line is still the file's own (conductor_line): its export copies the file as it is."""
    return all(json.dumps((tempo or {}).get(k) or []) == json.dumps(line.get(k) or []) for k in keys)


def tempo_tracks(lm, kinds=(TEMPO,)):
    """The numbers of the tracks holding meta events of these kinds (the file's tempo list: often copied into
    several tracks)."""
    return [t.number for t in lm.tracks if np.isin(t.m_type, kinds).any()]


def tempo_new(lm, tempo):
    """The Conductor's TEMPO (points / catch-ups, Keep timing too) isn't the file's own: its tempo events are
    written again (a signature changed alone leaves them as they are, hunt 2026-10-24)."""
    return not same_conductor(tempo, conductor_line(lm), ("points", "catch"))


def retime_for(lm, tempo, plan, targets):
    """midi_copy.Retime for exporting lm with a changed Conductor line (plan = tempo_file.file_plan's). A changed
    tempo: the file's tempo events left out, the new list put into each of targets (track numbers). Changed time /
    key signatures (or Keep timing moving the bars): the file's left out, the new ones put into the first track
    that had any (else targets' first). With Keep timing every event moved by the plan's map. What didn't change
    stays exactly as the file has it."""
    from files.midi_copy import Retime
    keep_map = plan["bp"] is not None
    tempo_on = tempo_new(lm, tempo)
    sigs_on = not same_conductor(tempo, conductor_line(lm), ("sigs", "keysigs")) or keep_map
    drop = ([TEMPO] if tempo_on else []) + ([TIME_SIG, KEY_SIG] if sigs_on else [])
    inserts = {}
    if tempo_on:
        for n in targets:
            inserts[n] = [(t, b"\xff\x51\x03" + int(u).to_bytes(3, "big")) for t, u in plan["events"]]
    if sigs_on:
        had = tempo_tracks(lm, (TIME_SIG, KEY_SIG))
        n = had[0] if had else (targets[0] if targets else 0)
        inserts.setdefault(n, [])
        inserts[n] += [(t, b"\xff\x58\x04" + bytes([top, power, 24, 8])) for t, top, power in plan["sigs"]]
        inserts[n] += [(t, b"\xff\x59\x02" + int(sf).to_bytes(1, "big", signed=True) + bytes([1 if mi else 0]))
                       for t, sf, mi in plan["keys"]]
    for events in inserts.values():
        events.sort(key=lambda e: e[0])  # (stable: on one tick the tempo first)
    return Retime(plan["bp"] if keep_map else None, drop, inserts, tempo_tracks(lm, tuple(drop)) if drop else [])


def own_hits(lm, s, e, lane, cancel=None, progress=None):
    """How many of Spiderweb's own notes (starts s, ends e, lanes = channel x 256 + key; the file's ticks) sound at
    the same time as a loaded note on the same channel + key (the export's heads-up: players can cut those, plan
    step 10). Each track is read only over the own notes' time, from its checkpoint before. cancel() True =
    Cancelled; progress(done, total) in bytes."""
    n = len(s)
    if not n:
        return 0
    order = np.lexsort((s, lane))
    s, e, lane = (np.ascontiguousarray(a[order], np.int64) for a in (s, e, lane))
    lane_at = np.searchsorted(lane, np.arange(QS + 1)).astype(np.int64)
    shift = lane << 50  # (ticks < 2^48: each lane's latest end so far in one go)
    top = np.maximum.accumulate(e + shift) - shift
    t0, t1 = int(s.min()), int(e.max())
    k_lo, k_hi = int((lane & 255).min()), int((lane & 255).max())
    hit = np.zeros(n, np.uint8)
    loops = speed.loops()
    busy = loops.midi_busy_hits if loops else midi_scan.busy_hits
    mf = lm.mf
    jobs = []
    for num, t in enumerate(lm.tracks):
        if not t.notes or t.end <= t0 or t.first >= t1 or t.khi < k_lo or t.klo > k_hi or not len(t.cp_off):
            continue
        j = max(0, int(np.searchsorted(t.cp_tick, t0, "right")) - 1)
        jobs.append((num, t, j, int(t.cp_off[j])))
    total, done = sum(t.limit - start for _, t, _, start in jobs), 0
    buf = bytearray(PIECE)
    with open(mf.path, "rb") as f:
        watch = mf.watch()
        for num, t, j, start in jobs:
            cnt, since = np.zeros(QS, np.int64), np.zeros(QS, np.int64)
            e0, e1 = t.cp_ent[j], t.cp_ent[j + 1]
            qs = t.e_q[e0:e1].astype(np.int64)
            cnt[qs] = t.e_cnt[e0:e1]
            since[qs] = t.cp_tick[j]
            st = np.zeros(midi_scan.BH_SIZE, np.int64)
            st[midi_scan.BH_TICK], st[midi_scan.BH_RUN] = t.cp_tick[j], t.cp_run[j]
            size = t.limit - start
            if t.data is not None:
                b = t.data[start:t.limit]
                busy(b, 0, len(b), t1, st, cnt, since, lane_at, s, e, top, hit)
                done += size
            elif size > 0:
                reader = mf.pieces_of(f, mf.tracks[num].start + start, size, buf, watch)
                filled, final = next(reader)
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    view = np.frombuffer(buf, np.uint8, filled) if loops else memoryview(buf)[:filled]
                    used = busy(view, 0, filled, t1, st, cnt, since, lane_at, s, e, top, hit)
                    del view
                    done += used
                    if progress:
                        progress(done, total)
                    if st[midi_scan.BH_STOP] or (final and used >= filled):
                        break
                    try:
                        filled, final = reader.send(used)
                    except StopIteration:
                        break
                reader.close()
            # (still sounding: to the own notes' end, or to the track's end when it ran out: shown up to there)
            at = t1 if st[midi_scan.BH_STOP] else max(int(st[midi_scan.BH_TICK]), t.last_tick)
            for q in np.flatnonzero(cnt > 0).tolist():
                a, z = int(lane_at[q]), int(lane_at[q + 1])
                if z > a and at > since[q]:
                    hit[a:z] |= ((s[a:z] < at) & (e[a:z] > since[q])).astype(np.uint8)
    lm.mf.same()
    return int(hit.sum())


def file_hash(mf):
    """One short code for every byte of a scanned file (its tracks' hashes + the rest's): a project knows the file
    again by it, wherever it's moved."""
    h = hashlib.blake2b(digest_size=16)
    for t in mf.tracks:
        h.update(t.hash.encode())
    h.update(str(mf.raw_hash).encode())
    return h.hexdigest()


def clean_link(link):
    """A project file's "loaded" block, checked: None when it can't be used."""
    if not isinstance(link, dict) or not isinstance(link.get("path"), str) or not link["path"]:
        return None
    out = {"path": link["path"], "hash": link.get("hash") if isinstance(link.get("hash"), str) else "",
           "mode": "memory" if link.get("mode") == "memory" else "file",
           "pairing": link.get("pairing") if isinstance(link.get("pairing"), str) else PAIRING}
    for k in ("size", "mtime", "ppq"):
        out[k] = link[k] if type(link.get(k)) is int else 0
    if isinstance(link.get("rel"), str) and link["rel"]:
        out["rel"] = link["rel"]
    return out


def located(link, folder):
    """Where a project's MIDI is now: its own path, else the same name relative to the project's folder (moved
    together), else just beside the project. None = not found."""
    tries = [link["path"]]
    if folder:
        if link.get("rel"):
            tries.append(os.path.normpath(os.path.join(folder, link["rel"])))
        tries.append(os.path.join(folder, os.path.basename(link["path"])))
    return next((p for p in tries if os.path.isfile(p)), None)


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
