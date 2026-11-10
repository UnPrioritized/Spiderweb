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
from files.midi_index import (CC_KINDS, CC_MOST, CP_LEAST, GO, GRID_MOST, MORE_CC, MORE_CP, MORE_ENT, MORE_GRID, MORE_KEYS,
                              MORE_META, MORE_POOL, META_KEEP, QS, X_CCOVER, X_END, X_FIRST, X_GHI, X_GLO, X_HANG, X_KHI,
                              X_KLO, X_LONG, X_NCC, X_NCP, X_NENT, X_NMETA, X_NOTES, X_ORPHAN, X_STOP, X_TICK, X_ZERO)
from files.midi_read import PIECE, Cancelled, MidiError, MidiFile

PIC_COLS = 1024  # a track's own picture: this many columns at most across its notes (+ its key range)
PLAIN_MOST = 16 << 20  # bytes a file may have to load without the speed-ups (plain Python: ~1-2 MB a second)
MARK_MOST = 1 << 26  # Keep timing's note ticks are marked this many ticks at a time (a byte each)
SPARE = 2 << 30  # "Keep in memory" only with this much memory left over (user 2026-10-23)
TEMPO, NAME, PORT, TIME_SIG, KEY_SIG = 0x51, 0x03, 0x21, 0x58, 0x59
PAIRING = "fifo"  # how note-ons and note-offs are paired (first on, first off: Domino, user 2026-10-23); saved
NO_LANES = (np.zeros(0, np.int64), np.zeros(0, np.int64))
EXTRACT_ASK, EXTRACT_MOST = 1_000_000, 10_000_000  # Extract: asked past this many notes, refused past that many
TAKEN_SPARE = 512 << 20  # bytes of Extract reads kept for boxes no shape holds now (undo / redo: no read again)


class TrackInfo:
    """What the pass found in one track."""
    __slots__ = ("number", "notes", "zero", "hang", "orphan", "long", "klo", "khi", "first", "end", "last_tick",
                 "chn", "channel", "cp_off", "cp_tick", "cp_run", "cp_ent", "e_q", "e_cnt", "e_end", "e_vel", "m_tick",
                 "m_type", "m_len", "m_data", "name", "pic", "pic_klo", "pic_glo", "pic_pf", "pic_cols", "data",
                 "limit", "stopped", "c_tick", "c_code", "c_kinds", "cc_over")
    # c_tick / c_code: its CC-type events (midi_index: code = status | data 1 << 8 | data 2 << 16), empty when
    # cc_over (more in the MIDI than CC_MOST: none kept); c_kinds = [[channel * CC_KINDS + kind, how many]] always

    def metas(self, kind):
        """[(tick, data bytes)] of this track's kept meta events of one kind (first META_KEEP bytes)."""
        at = np.flatnonzero(self.m_type == kind)
        return [(int(self.m_tick[i]), bytes(self.m_data[i, :min(int(self.m_len[i]), META_KEEP)])) for i in at]


class LoadedMidi:
    def __init__(self, mf, mode, G, infos, levels, seconds, edges=None):
        self.mf, self.path, self.mode = mf, mf.path, mode
        self.ppq, self.G = mf.ppq, G
        self.tracks = infos  # [TrackInfo] in file order
        self.levels = levels  # [(cnt, top)]: the overview, level n = buckets of G << n ticks
        # [uint8 like each level's top]: 2 = the top track has a note starting / ending there, 1 = only a track
        # under it does (edge_levels; the far-zoom picture's edges + shade)
        self.edges = edges if edges is not None else [np.zeros(t.shape, np.uint8) for c, t in levels]
        self.keys = levels[0][0].shape[0]  # 128, or 256 (a 256 keys MIDI)
        self.seconds = seconds
        self.from_index = False  # (opened from its saved index, files/midi_saved.py)
        self.notes = sum(t.notes for t in infos)
        self.end = max((t.end for t in infos if t.notes), default=0)  # the last note's end (ticks)
        self.last_tick = max((t.last_tick for t in infos), default=0)
        self.first = min((t.first for t in infos if t.notes), default=0)
        self._anchors = None  # (anchors(): the note ticks, read once when first needed)
        # (Extract: notes hidden, set_hidden. skip_all[skip_at[n]:skip_at[n + 1]] = track n's events left out
        # (sorted byte places); h_* = every hidden note by track, then note-on place; hide_ver = changes count)
        self.boxes, self._taken = (), {}
        self.hide_ver = 0
        self.rechannel = None  # (Track Properties' new channels: per track, -1 = its own; load_extract.sync_channels)
        self.port_moved = None  # (... new ports, the same way)
        self.gone = frozenset()  # (tracks removed in the track list: never drawn nor played; the same)
        self.dense = None  # (Kestrel (GPU)'s read-ahead of the parts too dense to read live: files/play_dense.py)
        self.cc_over = any(t.cc_over for t in infos)  # (more CC events than CC_MOST: none kept, only counted)
        self._set_hidden(np.zeros(0, np.int64), *(np.zeros(0, np.int64) for _ in range(6)))
        self._level0 = None

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
        n = sum(a.nbytes for c, t in self.levels for a in (c, t)) + sum(e.nbytes for e in self.edges)
        for t in self.tracks:
            n += sum(getattr(t, k).nbytes for k in ("cp_off", "cp_tick", "cp_run", "cp_ent", "e_q", "e_cnt", "e_end",
                                                    "e_vel", "m_tick", "m_type", "m_len", "m_data", "pic", "c_tick",
                                                    "c_code", "c_kinds"))
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

    def _set_hidden(self, trk, s, e, lane, vel, on, off):
        """The hidden notes (Extract) as the readers want them: sorted by track then note-on place."""
        o = np.lexsort((on, trk))
        self.h_trk, self.h_s, self.h_e, self.h_lane, self.h_vel, self.h_on, self.h_off = (
            a[o] for a in (trk, s, e, lane, vel, on, off))
        n = len(self.tracks)
        self.h_at = np.searchsorted(self.h_trk, np.arange(n + 1)).astype(np.int64)
        ev_t = np.concatenate([self.h_trk, self.h_trk[self.h_off >= 0]])
        ev_p = np.concatenate([self.h_on, self.h_off[self.h_off >= 0]])
        o = np.lexsort((ev_p, ev_t))
        self.skip_all = np.ascontiguousarray(ev_p[o], np.int64)
        self.skip_at = np.searchsorted(ev_t[o], np.arange(n + 1)).astype(np.int64)

    def frozen(self, hide=True, spare=()):
        """This MIDI with the notes hidden now kept as they are (an export in the background: undo / Extract while
        it runs change nothing in it); hide False = nothing hidden (exported as loaded). spare: box keys (box_key)
        whose notes are NOT hidden (an Extract left as it was: its notes stay in the file as they are, group D)."""
        return Frozen(self, hide, spare)

    def skip_of(self, n):
        """Track n's hidden events (sorted byte places in the track)."""
        return self.skip_all[self.skip_at[n]:self.skip_at[n + 1]]

    def hidden_open(self, n, at):
        """(lanes, counts) of track n's hidden notes sounding at byte place at (note-on before it, note-off at or
        after it / never): a read starting there takes them off its checkpoint's counts."""
        a, z = self.h_at[n], self.h_at[n + 1]
        if a == z:
            return NO_LANES
        on, off = self.h_on[a:z], self.h_off[a:z]
        k = int(np.searchsorted(on, at))  # (note-ons before it)
        open_ = (off[:k] >= at) | (off[:k] < 0)
        if not open_.any():
            return NO_LANES
        return np.unique(self.h_lane[a:z][:k][open_], return_counts=True)

    def channel_of(self, number):
        return self.tracks[number].channel

    def track_name(self, number):
        return self.tracks[number].name


class Frozen:
    """LoadedMidi.frozen's: everything as lm's, the hidden notes as they were when made."""
    skip_of, hidden_open = LoadedMidi.skip_of, LoadedMidi.hidden_open

    def __init__(self, lm, hide, spare=()):
        self._lm = lm
        got = [lm._taken[k] for k in spare if k in lm._taken] if hide else []
        src = lm if hide and not got else LoadedMidi.__new__(LoadedMidi)
        if not hide:
            src.tracks = lm.tracks
            z = np.zeros(0, np.int64)
            LoadedMidi._set_hidden(src, z, z, z, z, z, z, z)
        elif got:  # (every hidden note but the spared boxes')
            src.tracks = lm.tracks
            code = lm.h_trk * (1 << 40) + lm.h_on
            spared = np.concatenate([g["trk"] * (1 << 40) + g["on"] for g in got])
            keep = ~np.isin(code, spared)
            LoadedMidi._set_hidden(src, *(a[keep] for a in (lm.h_trk, lm.h_s, lm.h_e, lm.h_lane, lm.h_vel, lm.h_on,
                                                           lm.h_off)))
        for k in ("h_trk", "h_s", "h_e", "h_lane", "h_vel", "h_on", "h_off", "h_at", "skip_all", "skip_at"):
            setattr(self, k, getattr(src, k))

    def __getattr__(self, k):
        return getattr(self._lm, k)


def track_port(t):
    """A loaded track's port number (its first FF 21; none = 0)."""
    got = t.metas(PORT)
    return got[0][1][0] if got and got[0][1] else 0


def port_numbers(lm):
    """Each track's port number (track_port)."""
    return np.array([track_port(t) for t in lm.tracks], np.int64)


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
    step 10). Each track is read only over the own notes' time, from its checkpoint before. A track given a new
    channel in Track Properties (lm.rechannel) counts on it, removed ones (lm.gone) not at all. cancel() True =
    Cancelled; progress(done, total) in bytes."""
    n = len(s)
    if not n:
        return 0

    def prepared(s, e, lane):  # (sorted by lane, then start: (s, e, lane, lane_at, top, their order))
        order = np.lexsort((s, lane))
        s, e, lane = (np.ascontiguousarray(a[order], np.int64) for a in (s, e, lane))
        lane_at = np.searchsorted(lane, np.arange(QS + 1)).astype(np.int64)
        shift = lane << 50  # (ticks < 2^48: each lane's latest end so far in one go)
        top = np.maximum.accumulate(e + shift) - shift
        return s, e, lane, lane_at, top, order
    s, e, lane = (np.asarray(a, np.int64) for a in (s, e, lane))
    whole = prepared(s, e, lane)
    t0, t1 = int(s.min()), int(e.max())
    k_lo, k_hi = int((lane & 255).min()), int((lane & 255).max())
    hit = np.zeros(n, np.uint8)  # (in the given order)
    loops = speed.loops()
    busy = loops.midi_busy_hits if loops else midi_scan.busy_hits
    mf = lm.mf
    moved = lm.rechannel
    jobs = []
    for num, t in enumerate(lm.tracks):
        if (not t.notes or num in lm.gone or t.end <= t0 or t.first >= t1 or t.khi < k_lo or t.klo > k_hi
                or not len(t.cp_off)):
            continue
        j = max(0, int(np.searchsorted(t.cp_tick, t0, "right")) - 1)
        if moved is not None and num < len(moved) and moved[num] >= 0:
            # (its notes all on the new channel: the own notes there, read as if on each channel it was written on)
            mine = np.flatnonzero(lane >> 8 == moved[num])
            for ch in np.flatnonzero(t.chn).tolist():
                if len(mine):
                    got = prepared(s[mine], e[mine], ch * 256 + (lane[mine] & 255))
                    jobs.append((num, t, j, int(t.cp_off[j]), got[:5], mine[got[5]]))
        else:
            jobs.append((num, t, j, int(t.cp_off[j]), whole[:5], whole[5]))
    total, done = sum(job[1].limit - job[3] for job in jobs), 0
    buf = bytearray(PIECE)
    with open(mf.path, "rb") as f:
        watch = mf.watch()
        for num, t, j, start, (s, e, lane, lane_at, top), back in jobs:
            got = np.zeros(len(s), np.uint8)  # (hit, in this job's order)
            cnt, since = np.zeros(QS, np.int64), np.zeros(QS, np.int64)
            e0, e1 = t.cp_ent[j], t.cp_ent[j + 1]
            qs = t.e_q[e0:e1].astype(np.int64)
            cnt[qs] = t.e_cnt[e0:e1]
            since[qs] = t.cp_tick[j]
            lanes, counts = lm.hidden_open(num, start)  # (extracted: not sounding)
            cnt[lanes] -= counts
            skip = lm.skip_of(num)
            st = np.zeros(midi_scan.BH_SIZE, np.int64)
            st[midi_scan.BH_TICK], st[midi_scan.BH_RUN] = t.cp_tick[j], t.cp_run[j]
            size = t.limit - start
            if t.data is not None:
                b = t.data[start:t.limit]
                busy(b, 0, len(b), start, skip, t1, st, cnt, since, lane_at, s, e, top, got)
                done += size
            elif size > 0:
                reader = mf.pieces_of(f, mf.tracks[num].start + start, size, buf, watch)
                filled, final = next(reader)
                at = start
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    view = np.frombuffer(buf, np.uint8, filled) if loops else memoryview(buf)[:filled]
                    used = busy(view, 0, filled, at, skip, t1, st, cnt, since, lane_at, s, e, top, got)
                    del view
                    at += used
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
                    got[a:z] |= ((s[a:z] < at) & (e[a:z] > since[q])).astype(np.uint8)
            hit[back] |= got
    lm.mf.same()
    return int(hit.sum())


def _clean_group(v):
    if not isinstance(v, dict) or not isinstance(v.get("boxes"), list) or not isinstance(v.get("tracks"), list):
        return None
    boxes = [[int(x) for x in b] for b in v["boxes"][:256] if isinstance(b, list) and len(b) == 4
             and all(type(x) is int for x in b) and b[0] < b[1] and b[2] <= b[3]]
    tracks = sorted({int(n) for n in v["tracks"] if type(n) is int and 0 <= n < 65536})
    if not boxes or not tracks:
        return None
    out = {"boxes": boxes, "tracks": tracks}
    chans = clean_chans(v.get("chans"))
    if chans:  # (only these channels of its tracks: a part / a Ch row was shown, PLAN #11 D hunt)
        out["chans"] = chans
    if v.get("ports") is True:  # (some notes came from tracks on another port: the export writes ports)
        out["ports"] = True
    sig = v.get("sig")  # (its notes as made: load_extract.untouched)
    if isinstance(sig, str) and len(sig) == 32 and all(c in "0123456789abcdef" for c in sig):
        out["sig"] = sig
    return out


def clean_chans(v):
    """A box group's "chans" as read: sorted channels 0-15, each once; none / all 16 = [] (every channel)."""
    chans = sorted({c for c in v if type(c) is int and 0 <= c <= 15}) if isinstance(v, (list, tuple)) else []
    return chans if len(chans) < 16 else []


def extract_groups(v):
    """A shape's "extract" as a list of {"boxes", "tracks"(, "ports")} (one, or several when a shape took over the
    Extracts of others: Turn into plain notes / Slice of a merge holding them)."""
    if isinstance(v, list):
        return [g for g in (_clean_group(x) for x in v[:256]) if g is not None]
    g = _clean_group(v)
    return [g] if g is not None else []


def clean_extract(v):
    """A shape's "extract" (the loaded notes it took, Extract): {"boxes": [[t0, t1, k0, k1]] (ticks: note-ons in
    t0 .. t1 - 1; keys k0..k1), "tracks": [numbers]} or a list of them, checked; None when it can't be used."""
    groups = extract_groups(v)
    return None if not groups else groups[0] if len(groups) == 1 else groups


def held_extract(sh):
    """Every Extract shape sh holds (its own + its merge's parts'), as one "extract" value, or None: a shape made
    from its notes takes them over (the notes stay out of the loaded file)."""
    groups = extract_groups(sh.get("extract"))
    for p in _parts(sh):
        groups += extract_groups(held_extract(p))
    return clean_extract(groups)


def drop_extract(sh):
    """sh (a copy) holds no Extract any more, nor do its merge's parts at any depth."""
    sh.pop("extract", None)
    for p in _parts(sh):
        drop_extract(p)


def _parts(sh):
    return [p for k in ("merge", "unmerge") if isinstance(sh.get(k), dict) and isinstance(sh[k].get("parts"), list)
            for p in sh[k]["parts"] if isinstance(p, dict)]


def shape_boxes(shapes):
    """Every box the shapes' "extract" hold, in order: the loaded notes hidden. A Gate sensitive merge's shapes
    (kept inside it for Split, merge.py) count: its notes hold them."""
    out = []
    for sh in shapes:
        out += group_boxes(extract_groups(sh.get("extract")))
        out += shape_boxes(_parts(sh))
    return out


def group_boxes(groups):
    """The boxes of Extract-like groups ({"boxes", "tracks"}), as shape_boxes gives them (also App.deleted's: Delete
    loaded notes, group D)."""
    return [{"t0": b[0], "t1": b[1], "k0": b[2], "k1": b[3], "tracks": g["tracks"],
             **({"chans": g["chans"]} if g.get("chans") else {})} for g in groups for b in g["boxes"]]


def box_groups(boxes):
    """Boxes (box dicts) as Extract-like groups {"boxes", "tracks"(, "chans")}: one per tracks + channels."""
    out = {}
    for b in boxes:
        k = (tuple(b["tracks"]), tuple(clean_chans(b.get("chans"))))
        out.setdefault(k, []).append([b["t0"], b["t1"], b["k0"], b["k1"]])
    return [{"boxes": bs, "tracks": list(t), **({"chans": list(c)} if c else {})} for (t, c), bs in out.items()]


def extract_ports(shapes):
    """An Extract among the shapes holds notes from another port than the first (the export writes ports)."""
    return any(g.get("ports") for sh in shapes for g in extract_groups(sh.get("extract"))) or any(
        extract_ports(_parts(sh)) for sh in shapes)


def box_key(box):
    """An Extract box (a shape's "extract": t0, t1 ticks, k0, k1 keys, tracks, chans: () = every channel) as
    something to compare."""
    return (int(box["t0"]), int(box["t1"]), int(box["k0"]), int(box["k1"]), tuple(sorted(box["tracks"])),
            tuple(clean_chans(box.get("chans"))))


def boxes_meet(a, b):
    """Two Extract boxes could hold the same notes (time, keys and tracks all overlap)."""
    a, b = box_key(a), box_key(b)
    return a[0] < b[1] and b[0] < a[1] and a[2] <= b[3] and b[2] <= a[3] and bool(set(a[4]) & set(b[4])) and (
        not a[5] or not b[5] or bool(set(a[5]) & set(b[5])))


def gather_box(lm, box, cancel=None, progress=None, most=None, held=False):
    """The notes an Extract box takes (note-on in ticks t0 .. t1 - 1, keys k0..k1, its tracks), each whole:
    {"trk", "s", "e", "lane" (channel x 256 + key), "vel", "on", "off" (byte places; off -1 = never let go, e =
    its track's end)} sorted by start; notes hidden already (another box) left out. Each track read from its
    checkpoint before t0 until every note it took has ended. most: stops soon after taking more than this many
    in all (Extract refuses then: a box over a dense part never fills the memory first).
    held: also what's still sounding at t0 (Fill with tempo jumps' Top note, hunt 2026-10-29: a note held from
    before the area hid nothing): each track's notes taken from its checkpoint before t0 on, and the keys sounding
    at that checkpoint as "held_s" (the checkpoint's tick), "held_e" (their latest end), "held_lane"."""
    t0, t1, k0, k1, tracks, only = box_key(box)
    loops = speed.loops()
    run = loops.midi_gather if loops else midi_scan.gather
    mf = lm.mf
    chans = np.ones(16, np.uint8)
    if only:  # (some channels alone: a part / Ch row shown, PLAN #11 D hunt)
        chans[:] = 0
        chans[list(only)] = 1
    jobs, hold = [], []
    for n in tracks:
        t = lm.tracks[n] if 0 <= n < len(lm.tracks) else None
        if (t is None or not t.notes or t.end <= t0 or t.first >= t1 or t.khi < k0 or t.klo > k1
                or not len(t.cp_off) or not mf.tracks[n].whole):  # (a damaged track is only ever copied whole)
            continue
        j = max(0, int(np.searchsorted(t.cp_tick, t0, "right")) - 1)
        jobs.append((n, t, j, int(t.cp_off[j]), int(t.cp_tick[j]) if held else t0))
        if held:
            e0, e1 = t.cp_ent[j], t.cp_ent[j + 1]
            lane, end = t.e_q[e0:e1].astype(np.int64), t.e_end[e0:e1].astype(np.int64)
            lane, end = lane[t.e_cnt[e0:e1] > 0], end[t.e_cnt[e0:e1] > 0]
            ok = (chans[lane >> 8] > 0) & ((lane & 255) >= k0) & ((lane & 255) <= k1)
            end = np.where(end < 0, t.last_tick, end)[ok]
            hold.append((np.full(len(end), int(t.cp_tick[j]), np.int64), end, lane[ok]))
    total, done = sum(t.limit - start for _, t, _, start, _ in jobs), 0
    got, taken = [], 0
    buf = bytearray(PIECE)
    with open(mf.path, "rb") as f:
        watch = mf.watch()
        for n, t, j, start, tt0 in jobs:
            old, newer = np.zeros(QS, np.int64), np.zeros(QS, np.int64)
            e0, e1 = t.cp_ent[j], t.cp_ent[j + 1]
            old[t.e_q[e0:e1].astype(np.int64)] = t.e_cnt[e0:e1]  # (hidden ones too: gather pairs with every note)
            head, tail = np.full(QS, -1, np.int64), np.full(QS, -1, np.int64)
            size = 1 << 12
            o = [np.zeros(size, np.int64) for _ in range(8)]  # (s, e, lane, vel, on, off, next, was)
            o[7] = np.zeros(size, np.uint8)
            st = np.zeros(midi_scan.GA_SIZE, np.int64)
            st[midi_scan.GA_TICK], st[midi_scan.GA_RUN] = t.cp_tick[j], t.cp_run[j]
            skip = lm.skip_of(n)
            limit = t.limit - start
            reader = None
            if t.data is None:
                reader = mf.pieces_of(f, mf.tracks[n].start + start, limit, buf, watch)
                filled, final = next(reader)
            else:
                filled, final = limit, True
            at = 0  # (the bytes read so far, before the piece)
            while True:
                if cancel and cancel():
                    raise Cancelled()
                if reader is None:
                    view = t.data[start + at:t.limit]
                else:
                    view = np.frombuffer(buf, np.uint8, filled) if loops else memoryview(buf)[:filled]
                used = run(view, 0, len(view), start + at, skip, tt0, t1, k0, k1, chans, st, old, newer, head, tail,
                           o[6], o[0], o[1], o[2], o[3], o[4], o[5], o[7])
                del view
                at += used
                done += used
                if progress:
                    progress(done, total)
                if st[midi_scan.GA_DONE]:
                    break
                full = st[midi_scan.GA_N] >= len(o[0])
                if full:  # (bigger, on from where it stopped)
                    if st[midi_scan.GA_N] > EXTRACT_MOST or most is not None and taken + st[midi_scan.GA_N] > most:
                        break
                    o = [np.concatenate([a, np.zeros(len(a), a.dtype)]) for a in o]
                    if reader is None:
                        continue
                elif reader is None or (final and used >= filled):
                    break
                try:
                    filled, final = reader.send(used)
                except StopIteration:
                    break
            if reader is not None:
                reader.close()
            m = int(st[midi_scan.GA_N])
            s, e, lane, vel, on, off, was = (a[:m] for a in (o[0], o[1], o[2], o[3], o[4], o[5], o[7]))
            e = np.where(e < 0, max(int(st[midi_scan.GA_TICK]), t.last_tick), e)  # (never let go: its track's end)
            keep = was == 0
            got.append((np.full(int(keep.sum()), n, np.int64), s[keep], e[keep], lane[keep], vel[keep], on[keep],
                        off[keep]))
            taken += int(keep.sum())
            if most is not None and taken > most:
                break
    mf.same()
    keys = ("trk", "s", "e", "lane", "vel", "on", "off")
    out = {k: np.zeros(0, np.int64) for k in keys}
    if got:
        cols = [np.concatenate([g[i] for g in got]) for i in range(7)]
        o = np.argsort(cols[1], kind="stable")
        out = {k: c[o] for k, c in zip(keys, cols)}
    if held:
        for i, k in enumerate(("held_s", "held_e", "held_lane")):
            out[k] = np.concatenate([h[i] for h in hold]) if hold else np.zeros(0, np.int64)
    return out


EDO_MOST = 4_000_000  # notes a loaded MIDI may have to be played in 31-EDO (edo_rows: all read into memory)


def edo_notes(lm):
    """Every note of lm as (track, start, end, key, channel, velocity) arrays, for playing it in 31-EDO (user
    2026-11-10: forced, one piano sound): removed tracks and Extract's hidden notes left out, keys up to 255.
    Read once per hidden set (gather_box over the whole song), kept on lm."""
    key = (lm.hide_ver, lm.gone)
    got = getattr(lm, "_edo_notes", None)
    if got is not None and got[0] == key:
        return got[1]
    tracks = [n for n in range(len(lm.tracks)) if n not in lm.gone]
    g = gather_box(lm, {"t0": 0, "t1": lm.last_tick + 1, "k0": 0, "k1": 255, "tracks": tracks})
    lane = g["lane"]
    # (small types: kept beside the rows, ~23 bytes a note instead of 48)
    out = (g["trk"].astype(np.int32), g["s"].astype(np.int64), g["e"].astype(np.int64), (lane & 255).astype(np.uint8),
           (lane >> 8).astype(np.uint8), g["vel"].astype(np.uint8))
    del g, lane
    lm._edo_notes = (key, out)
    return out


def edo_forget(lm):
    """The 31-EDO copies of lm's notes let go (the Loaded MIDI tick / 31-EDO / Audio off: ~0.4 GB at 4 M notes)."""
    for name in ("_edo_notes", "_edo_rows"):
        if getattr(lm, name, None) is not None:
            setattr(lm, name, None)


def edo_left_out(lm):
    """(pitch bends, instrument changes, controller changes: CC + key / channel pressure) in lm's tracks (removed
    ones not counted): what playing it in 31-EDO leaves out. From the load's own counts, nothing read."""
    from files import midi_scan as S
    got = getattr(lm, "_edo_left", None)
    if got is not None and got[0] == lm.gone:
        return got[1]
    c = np.zeros(S.C_CHANNELS, np.int64)  # (counts up to C_BEND)
    for n, t in enumerate(lm.mf.tracks):
        if n not in lm.gone and t.counts is not None:
            got = np.asarray(t.counts, np.int64)[:len(c)]
            c[:len(got)] += got
    out = (int(c[S.C_BEND]), int(c[S.C_PROG]), int(c[S.C_CC] + c[S.C_PRESS] + c[S.C_POLY]))
    lm._edo_left = (lm.gone, out)
    return out


def edo_rows(lm, muted, ppq):
    """lm's notes as the player's rows (start, end, key row, velocity, slot, owner -1) in ppq's ticks, the muted
    tracks / channels (TrackList.loaded_mutes) left out. slot = OWN0 + the file channel, so engine.channels_of gives
    the channel it's played on (31-EDO's channel groups: playback.edo_code). Kept on lm until something it reads
    changes."""
    key = (lm.hide_ver, lm.gone, tuple(muted), ppq)
    got = getattr(lm, "_edo_rows", None)
    if got is not None and got[0] == key:
        return got[1]
    trk, s, e, k, ch, vel = edo_notes(lm)
    if len(muted):
        from files.play_loaded import mute_masks
        mask = mute_masks(muted, len(lm.tracks)).astype(np.int64)
        keep = ((mask[trk] >> ch) & 1) == 0
        trk, s, e, k, ch, vel = (a[keep] for a in (trk, s, e, k, ch, vel))
    if ppq != lm.ppq:  # (the project's ticks: the same beats)
        s, e = s * ppq // lm.ppq, e * ppq // lm.ppq
    from notes.engine import OWN0
    rows = np.column_stack([s, e, k, vel, OWN0 + ch.astype(np.int64), np.full(len(s), -1, np.int64)]).astype(np.int64)
    lm._edo_rows = (key, rows)
    return rows


def set_boxes(lm, boxes, found=None):
    """The loaded notes hidden = the ones these Extract boxes took (the shapes holding them now, in order); a box
    not seen before is gathered (found = {box_key: gather_box's} made already). The overview loses them too."""
    keys = [box_key(b) for b in boxes]
    if tuple(keys) == lm.boxes:
        return False
    taken = {}
    lm._taken.update(found or {})  # (boxes read are kept: undo / redo bring them back without reading)
    for b, k in zip(boxes, keys):
        if k in taken:
            continue
        got = lm._taken.pop(k, None)
        if got is None:
            got = gather_box(lm, b)
        taken[k] = lm._taken[k] = got  # (newest used last: the ones dropped first are the longest unused)
    spare = sum(_bytes(g) for k, g in lm._taken.items() if k not in taken)
    for k in [k for k in lm._taken if k not in taken]:
        if spare <= TAKEN_SPARE:
            break
        spare -= _bytes(lm._taken.pop(k))
    lm.boxes = tuple(keys)
    parts = list(taken.values())
    cols = [np.concatenate([np.zeros(0, np.int64)] + [p[c] for p in parts]) for c in
            ("trk", "s", "e", "lane", "vel", "on", "off")]
    if len(parts) > 1:  # (boxes of one Extract may overlap: each note once)
        _, first = np.unique(np.column_stack([cols[0], cols[5]]), axis=0, return_index=True)
        if len(first) < len(cols[0]):
            first.sort()
            cols = [c[first] for c in cols]
    lm._set_hidden(*cols)
    _hide_overview(lm, cols[0], cols[1], cols[2], cols[3])
    lm.hide_ver += 1
    return True


def _bytes(got):
    return sum(a.nbytes for a in got.values())


def edge_levels(etop, levels):
    """Each overview level's edges (LoadedMidi.edges) from etop (level 0: the highest track with a note starting /
    ending in each bucket; halved by the highest of two, like top): 2 where that's the top track, 1 under it
    (user 2026-10-25: far zoomed out a giant song's dense notes showed as one note running across)."""
    out = []
    for n, (cnt, top) in enumerate(levels):
        if n:
            if etop.shape[1] % 2:
                etop = np.concatenate([etop, np.full((etop.shape[0], 1), -1, etop.dtype)], axis=1)
            etop = etop.reshape(etop.shape[0], -1, 2).max(axis=2)
        out.append(np.where(etop < 0, 0, np.where(etop >= top, 2, 1)).astype(np.uint8))
    return out


def _hide_overview(lm, trk, s, e, lane):
    """The overview without the hidden notes: each key's sounding count taken down over the buckets they cover
    (as the loading pass counted them), a bucket with none left = empty; the halved levels made again."""
    if lm._level0 is None:
        if not len(trk):
            return
        lm._level0 = tuple(a.copy() for a in lm.levels[0])
    cnt, top = (a.copy() for a in lm._level0)
    G, ng = lm.G, cnt.shape[1]
    g0 = s // G
    ok = g0 < GRID_MOST - 1
    if ok.any():
        k = (lane & 255)[ok]
        ee = np.maximum(e, s + 1)[ok]
        a, z = g0[ok], np.minimum((ee - 1) // G + 1, GRID_MOST - 1)
        d = np.zeros((cnt.shape[0], ng + 1), np.int64)
        np.add.at(d, (k, np.minimum(a, ng)), 1)
        np.add.at(d, (k, np.minimum(z, ng)), -1)
        cnt = np.maximum(cnt - np.cumsum(d, axis=1)[:, :ng], 0).astype(cnt.dtype)
        top = np.where(cnt > 0, top, -1).astype(top.dtype)
    loops = speed.loops()
    halve = loops.midi_halve if loops else midi_index.halve
    levels = [(cnt, top)]
    while cnt.shape[1] > 1:
        n2 = (cnt.shape[1] + 1) // 2
        c2, t2 = np.zeros((cnt.shape[0], n2), np.int32), np.zeros((cnt.shape[0], n2), np.int32)
        halve(cnt, top, c2, t2)
        levels.append((c2, t2))
        cnt, top = c2, t2
    lm.levels = levels


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
        self.edg = np.zeros((nk, ng), np.uint8)
        self.chn = np.zeros(16, np.int64)
        self.ccn = np.zeros(16 * CC_KINDS, np.int64)

    def clear(self):
        self.q_hd.fill(-1)
        self.q_tl.fill(-1)
        self.q_rl.fill(0)
        self.q_pos.fill(-1)
        self.chn.fill(0)
        self.ccn.fill(0)


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
        self.etop = np.full((nk, 1024), -1, np.int32)
        self.started = time.perf_counter()
        self.cc_kept = 0  # (CC events kept so far, all tracks: past CC_MOST none are kept)
        self.cc_over = False

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
        self.started = time.perf_counter()  # (the reading's time only: never the wait above, hunt 2026-10-25)
        self.stage = "read"
        if not self.keep:  # (its index saved by an earlier load: a moment, no read of the file, step 12)
            from files.midi_saved import load_index
            lm = load_index(self.mf)
            if lm is not None:
                lm.seconds, lm.from_index = time.perf_counter() - self.started, True
                self.done, self.tracks_done, self.notes_found = self.total, len(lm.tracks), lm.notes
                return lm
        loops = speed.loops()
        self._index = loops.midi_index if loops else midi_index.index
        self._close = loops.midi_close if loops else midi_index.close
        self._grid = loops.midi_grid if loops else midi_index.grid
        self.mf.scan(progress=self._progress, cancel=lambda: self.cancelled, each=self._each)
        self.stage = "overview"
        if self.cancelled:
            raise Cancelled()
        levels, edges = self._levels(loops)
        if self.cc_over:  # (tracks done before it went over kept theirs: none kept, the same for every track)
            for i in self._infos:
                i.c_tick, i.c_code, i.cc_over = np.zeros(0, np.int64), np.zeros(0, np.int32), True
        return LoadedMidi(self.mf, "memory" if self.keep else "file", self.G, self._infos, levels,
                          time.perf_counter() - self.started, edges)

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
            cnt, top, etop = self.cnt[:, :ng].copy(), self.top[:, :ng].copy(), self.etop[:, :ng].copy()
        self.cnt = self.top = self.etop = None
        levels = [(cnt, top)]
        while cnt.shape[1] > 1:
            n2 = (cnt.shape[1] + 1) // 2
            c2, t2 = np.zeros((cnt.shape[0], n2), np.int32), np.zeros((cnt.shape[0], n2), np.int32)
            halve(cnt, top, c2, t2)
            levels.append((c2, t2))
            cnt, top = c2, t2
        return levels, edge_levels(etop, levels)

    def grow_grid(self, nk, ng):
        """The overview grows to nk keys x ng buckets at least (under the lock)."""
        k0, g0 = self.cnt.shape
        if nk <= k0 and ng <= g0:
            return
        nk, ng = max(nk, k0), max(ng, g0 * 2)
        cnt = np.zeros((nk, ng), np.int32)
        top = np.full((nk, ng), -1, np.int32)
        etop = np.full((nk, ng), -1, np.int32)
        cnt[:k0, :g0] = self.cnt
        top[:k0, :g0] = self.top
        etop[:k0, :g0] = self.etop
        self.cnt, self.top, self.etop = cnt, top, etop


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
        self.c_tick = np.zeros(16, np.int64)
        self.c_code = np.zeros(16, np.int32)
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
                                      self.e_q, self.e_cnt, self.e_end, self.e_vel, self.e_nx, th.cov, th.edg, th.chn,
                                      self.m_tick, self.m_type, self.m_len, self.m_data, self.c_tick, self.c_code,
                                      th.ccn)
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
        elif status == MORE_CC:
            n = min(len(self.c_tick) * 2, midi_index.CC_MOST)  # (the read's own limit: it stops asking there)
            self.c_tick = np.concatenate([self.c_tick, np.zeros(n - len(self.c_tick), np.int64)])
            self.c_code = np.concatenate([self.c_code, np.zeros(n - len(self.c_code), np.int32)])
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
        edg = np.zeros(cov.shape, np.uint8)
        edg[:k0, :g0] = th.edg
        th.cov, th.edg = cov, edg

    def done(self):
        L, t, th, ix = self.L, self.t, self.th, self.ix
        self.cover(th.cov.shape[0], min(int(ix[X_TICK]) // L.G + 3, GRID_MOST))
        L._close(ix, L.G, th.q_hd, th.q_rl, th.opened, th.pt, th.pn, th.pw, self.e_end, self.e_nx, th.cov, th.edg)
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
        ncc = int(ix[X_NCC])
        kinds = np.flatnonzero(th.ccn)
        i.c_kinds = np.stack([kinds, th.ccn[kinds]], 1).astype(np.int64)
        with L._lock:
            L.cc_kept += ncc
            L.cc_over = L.cc_over or bool(ix[X_CCOVER]) or L.cc_kept > CC_MOST
            i.cc_over = L.cc_over
        i.c_tick = np.zeros(0, np.int64) if i.cc_over else self.c_tick[:ncc].copy()
        i.c_code = np.zeros(0, np.int32) if i.cc_over else self.c_code[:ncc].copy()
        self.c_tick = self.c_code = None
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
                L._grid(th.cov, th.edg, glo, ghi, i.klo, i.khi, L.cnt, L.top, L.etop, t.number, pic, pf)
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
