"""Playing a loaded MIDI (Load MIDI group D, user 2026-10-24): its events are read from the file (or the bytes kept
in memory) a little ahead of the play line and sent as the file has them, every note-on and note-off (user picked:
"play the file exactly as written", like black MIDI players). The Player (files/playback.py) playing Spiderweb's
own shapes is the clock: the same play line, the same tempo map (tempo_file.heard_tempo with the file's tempo).

Too many notes for the synth: a note budget per step keeps the loudest (the quietest left out first, each with
its note-off); notes too late (the reading or the synth behind) are left out too, so the line never lags and no
key sticks (user picked: keep real time). Measured 2026-10-24 (dev/scratch/play_measure*.py): one BASSMIDI synth
keeps up with ~1 M notes a second at 1,000 voices, so Built-in BASSMIDI plays a loaded MIDI on a LiveGroup (16
synths side by side, each on its own core: 10 M notes a second at ~10 % each); other MIDI outs (one device, 16
channels) get a small budget (Python hands them one message at a time).

Reader thread: per track a place in its bytes, started from the checkpoint before the start (midi_index: the notes
sounding there are counted, so their note-offs are left out; the controllers since that checkpoint are sent
first), events of all tracks put in tick order a window of ticks at a time. Feeder thread: every few ms, the
events up to the play line's tick, through the budget, as raw bytes to each synth (midi_play.emit). Falling far
behind (Shinki's wall: hundreds of millions of notes in a beat) = a jump: every sound stopped, reading starts
again just ahead of the line."""

import ctypes
import os
import threading
import time
from collections import deque

import numpy as np

from files import midi_play, speed
from files.synth import GROUP_BUFFER, RATE
from files.midi_scan import (C_BANK, C_BEND, C_BTICK, C_CC, C_OFF, C_ON, C_ON0, C_POLY, C_PRESS, C_PROG, C_PTICK,
                             C_PVAL)

STEP_S = 0.003  # the feeder's beat
AHEAD_S = 1.0  # read this far ahead of the play line at most
LATE_S = 0.12  # a note-on this late is left out
BEHIND_S = 1.5  # the reading this far behind the line = a jump ahead
JUMP_S = 0.3  # ... to this far ahead of the line
CHUNK = 4 << 20  # bytes of one track read at a time at most
BATCH = 64 << 20  # bytes of many tracks read for one call of the compiled loop
OUT = 1 << 20  # bytes each synth's message buffer holds
BACKLOG = 1 << 20  # bytes waiting in a synth past which it gets no new notes (it is behind; BASS takes them in
# bursts, so 256 KB was passed in normal playing)
WANT = 4_000_000  # events a reading window aims at (its length in ticks follows)
WINDOW_S = 0.25  # a reading window is this much of the song at most
SPAN_MOST = 1 << 22  # a window's ticks at most (the counting sort's table)
BUDGET_SYNTH = 200_000  # note-ons a second to start from on each of Built-in BASSMIDI's synths (then by its load)
BUDGET_KDMAPI = 200_000  # ... OmniMIDI (KDMAPI): one message per call from Python
BUDGET_DEVICE = 30_000  # ... a Windows MIDI device
BUDGET_LEAST, BUDGET_MOST = 20_000, 1_500_000  # (a synth: one makes ~1 M notes a second at 1,000 voices, measured)
# A synth's load reading LAGS (measured 2026-10-24, Shinki beats 1100-1125, 8 synths x 500 voices: budgets grew to 1.5 M
# each, the synths jammed up to 440 % and lost ~4.5 s of sound in 25 s). So each synth's CEILING is learned from the
# sound it really loses (any PC: user asked; position vs the clock): sound lost = budget halved, ceiling = LOST_CAP of
# what it was, ONLY while that synth was full of notes (thinned / backed up in that check: hunt 2026-10-25, a device
# hiccup or one wall left light MIDIs thinned for 20-40 s); no loss for a while = the ceiling climbs back (CAP_RISE
# each check). Kept for the next play per synth count + voices + soundfont (_caps).
LOST_S = 0.005  # sound missing in one check (~0.1 s) that counts as lost
LOST_CAP = 0.7
CAP_RISE = 1.05  # (~60 % a second once calm: back from a wrong lowering within a few seconds)
GROW = 1.08  # a budget growing: this much each check
WARM_S = 2.0  # sound lost this soon after the line starts never lowers a ceiling (start-up loses some at any load)
_caps = {}  # (synths, voices each, soundfont) -> each synth's learned ceiling, for the next play
SKIP = 64 * 16 * 128  # (port x channel x key: note-offs still to leave out)
POOL = 12  # windows' arrays kept for use again at most
POOL_LONGEST = 1 << 23  # ... each this long at most (a wall's big ones go)
WINDOW_MOST = 4 * WANT  # a window holding more events is read again shorter (one tick: stepped over, counted only)
QUEUE_MOST = 8 * WANT  # events read ahead at most (hunt 2026-10-25: a wall's offs read ahead took 16-30 GB)
NO_COUNT = np.zeros(1, np.int64)
READY_S = 0.5  # seconds of the song read before the play line starts
SIDE_SPAN = 1 << 18  # a window of more ticks is put in order on one thread (the side by side way's tables: ticks x
# threads)


def helper_count():
    """Threads reading a loaded MIDI beside the reader: a quarter of the cores, 0-3 (Kestrel's own rankers, its
    render thread, BASS and the window need theirs)."""
    return max(0, min(3, (os.cpu_count() or 4) // 4 - 1))


class _Scratch:
    """One reading thread's own memory: the bytes read, the events found (grown, never made anew for each window:
    fresh memory cost more than the reading itself), its own handle on the file."""

    def __init__(self, path=None):
        self.buf = None
        self.file = open(path, "rb") if path else None
        self.t, self.m, self.k = np.empty(0, np.int64), np.empty(0, np.int32), np.empty(0, np.int32)

    def room(self, n, more):
        """The arrays hold n events + more."""
        if n + more > len(self.t):
            size = max(n + more, 2 * len(self.t), 1 << 20)

            def grow(a, kind):
                return np.concatenate([a[:n], np.empty(size - n, kind)])
            self.t, self.m, self.k = grow(self.t, np.int64), grow(self.m, np.int32), grow(self.k, np.int32)
        return n

    def close(self):
        if self.file is not None:
            self.file.close()


def _loops():
    fast = speed.loops()
    if fast is not None:
        return (fast.play_read, fast.play_order, fast.play_hist, fast.play_emit, fast.play_programs, fast.play_stamp,
                fast.play_order_count, fast.play_order_place, fast.play_condense, fast.play_stamp_pre)
    return (midi_play.read, midi_play.order, midi_play.hist, midi_play.emit, midi_play.programs, midi_play.stamp,
            midi_play.order_count, midi_play.order_place, midi_play.condense, midi_play.stamp_pre)


_SONGS = 0  # (loaded MIDIs numbered as Kestrel first plays them: LoadedPlayer._song)


def _tm_arrays(tmap):
    """A TimeMap as arrays (ticks, seconds, seconds a tick), for the compiled loops."""
    return (np.asarray(tmap.ticks, np.int64), np.asarray(tmap.secs, np.float64), np.asarray(tmap.spt, np.float64))


PUSH_S = 1.0  # Kestrel (GPU): events pushed this far ahead of the line (ranking a dense push takes a while)
PIECE_BLOCKS = 8  # ... at most this many of its blocks a push
HOLD_BLOCKS = 4  # ... a block not whole yet waits at most until it's this close to being made


def instruments(lm):
    """(programs, drum kits) lm plays: its program changes' and the piano / drum kit 0 every channel starts with."""
    progs, kits = lm.mf.programs()
    return progs | {0}, kits | {0}


def preload(synth, lm):
    """The sounds of every instrument lm plays loaded BEFORE playing (user 2026-10-25: playback follows the file's
    instruments; samples loaded while playing, in BASS's threads, crashed with an SFZ before), each from the
    soundfont it's heard from; none has it = silent, like other players. Slow the first time (a whole piano from a
    hard disk: seconds): App.start_loaded runs it in the background when presets_missing."""
    synth.load_presets(*instruments(lm))


def track_ports(lm):
    """(port index per track (0 .. ports - 1, by the file's FF 21 port numbers; none = port 0), ports used)."""
    from notes.loaded import port_numbers
    raw = port_numbers(lm).tolist()
    used = sorted(set(raw)) or [0]
    return np.array([used.index(p) for p in raw], np.int64), len(used)


def playable(mt):
    """A track with channel events to play (midi_scan's counts)."""
    c = mt.counts
    return c is not None and int(c[C_ON] + c[C_OFF] + c[C_ON0] + c[C_POLY] + c[C_CC] + c[C_PROG] + c[C_PRESS]
                                 + c[C_BEND]) > 0


class LoadedPlayer:
    """Plays lm along with player (its clock). sinks: a LiveGroup's Lives (raw bytes each) or None = out (a MidiOut,
    one message at a time). muted() = the track numbers not to play now (asked every 100 ms)."""

    def __init__(self, lm, player, out, sinks=None, kind="device", muted=lambda: ()):
        self.lm, self.player, self.out, self.sinks = lm, player, out, sinks
        self.synths = len(sinks) if sinks else 1
        # note-ons a second each synth may take (Built-in BASSMIDI's: then set by each one's load)
        self.budgets = np.full(self.synths, {"group": BUDGET_SYNTH, "kdmapi": BUDGET_KDMAPI}.get(kind, BUDGET_DEVICE),
                               np.int64)
        self.muted_of = muted
        self.kind = kind
        if sinks or kind == "kestrel":  # (Kestrel has 16 ports: the file's own)
            self.tport, self.ports = track_ports(lm)
        else:  # (one device: 16 channels, every port onto them)
            self.tport, self.ports = np.zeros(len(lm.tracks), np.int64), 1
        self.muted = np.zeros(len(lm.tracks), np.uint8)
        moved = lm.rechannel  # (Track Properties' channels, per track: -1 = as written)
        self.chans = None if moved is None else np.asarray(moved, np.int64).copy()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._queue = deque()  # [ticks, messages, tracks, place reached, skips to add, arrays] windows read, in order
        self._pool = []  # (arrays of windows played: used again, never made anew each window)
        self._gen = 0  # (a jump: windows read before it are thrown away)
        self._jump = None  # tick the reader starts again from (asked by the feeder)
        self._unpart = None  # (Kestrel) (generation, part, tick): a part read ahead stopped there (_leave_part)
        self.thinned = 0  # note-ons left out (budget / late), so far
        self.sent = 0  # note-ons sent
        self.jumps = 0
        self.walls = 0  # (ticks stepped over: more events than a window may hold)
        self.dense_parts = 0  # (Kestrel: parts played from their read-ahead)
        self.dropped = 0  # (times a jammed synth's waiting messages were thrown away)
        self.windows, self.read_s = 0, 0.0  # (windows read, seconds reading them took: measuring)
        self.error = None
        self.threads = []
        self.ready = threading.Event()  # (set once the first notes are read: Player.ready, the line waits for it)
        self.prof = [0.0, 0.0, 0, 0, 0.0]  # (Kestrel feeder, measuring: seconds stamping, pushing, pushes, steps with
        # nothing read, how far the reading was short of the push)
        self._place_s = 0.0  # (seconds finding every track's place took last time: a jump goes that much further)
        # (the reader's own memory + file, its helper threads': made when it starts)
        self._sc, self._helpers, self._pool_x, self._file = _Scratch(), [], None, None

    # --- the reader ---------------------------------------------------------------------------------------------

    def _table(self):
        """Every playable track's arrays, one place per track ("slot"): number, bytes, checkpoints put together
        (cp_key = slot x M + tick, so one search finds each track's own)."""
        lm = self.lm
        # (removed in the track list: none of its events, like the export)
        num = [n for n, (t, mt) in enumerate(zip(lm.tracks, lm.mf.tracks)) if playable(mt) and t.limit > 0
               and n not in lm.gone]
        self.num = np.array(num, np.int64)
        self.start_of = np.array([lm.mf.tracks[n].start for n in num], np.int64)
        self.limit = np.array([lm.tracks[n].limit for n in num], np.int64)
        self.M = max([int(lm.tracks[n].last_tick) for n in num] + [0]) + 2
        self.num32 = self.num.astype(np.int32)
        cps = [lm.tracks[n].cp_tick for n in num]
        counts = np.array([len(c) for c in cps], np.int64)
        self.seg0 = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64) if len(num) else counts
        self.seg1 = self.seg0 + counts
        slot = np.repeat(np.arange(len(num), dtype=np.int64), counts)
        cat = (lambda a: np.concatenate(a).astype(np.int64)) if len(num) else (lambda a: np.zeros(0, np.int64))
        self.cp_tick = cat(cps)
        self.cp_key = slot * self.M + self.cp_tick
        self.cp_off = cat([lm.tracks[n].cp_off for n in num])
        self.cp_run = cat([lm.tracks[n].cp_run for n in num])
        self.mode_file = lm.mode != "memory"
        # (Extract: the hidden events, read as they were when playing started)
        self.sk = lm.skip_all
        self.sk_end = lm.skip_at[self.num + 1] if len(num) else np.zeros(0, np.int64)

    def _fill(self, buf, slots, at, size, f=None):
        """buf[at[j]:at[j] + size[j]] = slot j's bytes from its place (file: one read each, from f or the reader's
        own; kept bytes: copied)."""
        if self.mode_file:
            f = f or self._file
            view = memoryview(buf)
            for s, a, z in zip(slots.tolist(), at.tolist(), size.tolist()):
                f.seek(int(self.start_of[s] + self.pos[s]))
                got = f.readinto(view[a:a + z])
                if got < z:  # (the file is shorter now: changed under it, still_same tells)
                    buf[a + got:a + z] = 0
            return
        for s, a, z in zip(slots.tolist(), at.tolist(), size.tolist()):
            p = int(self.pos[s])
            buf[a:a + z] = self.lm.tracks[int(self.num[s])].data[p:p + z]

    def _read(self, slots, t1, most=None, count=None, sc=None):
        """Slots' events up to tick t1 (their places moved on): (ticks, messages, track numbers), track by track.
        most: more events than this = None (stopped part way: the caller puts the places back and reads less).
        count = the notes sounding per slot x channel x key, notes only counted there (not kept). sc = the
        _Scratch read into (a helper thread's; else the reader's own), the arrays returned are its."""
        read = _loops()[0]
        sc = sc or self._sc
        n = 0
        counting = count is not None
        if count is None:
            count = NO_COUNT
        while len(slots):
            # (up to the first checkpoint at or past t1: every event before t1 is before it)
            j2 = np.searchsorted(self.cp_key, slots * self.M + t1, "left")
            end = np.where(j2 < self.seg1[slots], self.cp_off[np.minimum(j2, len(self.cp_off) - 1)]
                           if len(self.cp_off) else 0, self.limit[slots])
            size = np.minimum(np.maximum(np.maximum(end - self.pos[slots], 256), self.want[slots]),
                              np.maximum(CHUNK, self.want[slots]))
            size = np.minimum(size, self.limit[slots] - self.pos[slots])
            gone = size <= 0
            self.done[slots[gone]] = True
            slots, size, end = slots[~gone], size[~gone], end[~gone]
            if not len(slots):
                break
            more = []
            for lo in range(0, len(slots), 4096):  # (batches: the buffer stays small)
                ss, zz = slots[lo:lo + 4096], size[lo:lo + 4096]
                cut = np.searchsorted(np.cumsum(zz), BATCH // 8 if counting else BATCH, "right") + 1  # (about BATCH
                # bytes a call; counting: the room kept for what it may hold stays small)
                ss, zz = ss[:cut], zz[:cut]
                if cut < len(slots[lo:lo + 4096]):
                    more.append(slots[lo:lo + 4096][cut:])
                at = np.concatenate([[0], np.cumsum(zz)[:-1]]).astype(np.int64)
                total = int(zz.sum())
                if sc.buf is None or len(sc.buf) < total:
                    sc.buf = np.empty(max(total, 1 << 22), np.uint8)
                self._fill(sc.buf, ss, at, zz, sc.file)
                tick, run = self.tick[ss].copy(), self.run[ss].copy()
                nxt, used = np.empty(len(ss), np.int64), np.empty(len(ss), np.int64)
                sk_i = self.sk_i[ss].copy()
                n = sc.room(n, total // 2 + 2 * len(ss))
                n = read(sc.buf, at, zz, t1, tick, run, nxt, used, self.num32[ss], sc.t, sc.m, sc.k, n,
                         self.pos[ss], self.sk, sk_i, self.sk_end[ss], counting, count, ss)
                self.tick[ss], self.run[ss], self.nxt[ss], self.sk_i[ss] = tick, run, nxt, sk_i
                self.pos[ss] += used
                # what this window still needs of them: cut short by the chunk / an event longer than read
                done = self.pos[ss] >= self.limit[ss]
                stuck = (used == 0) & (nxt < 0) & ~done
                ends_here = stuck & (self.pos[ss] + zz >= self.limit[ss])  # (cut short at the track's end)
                done |= ends_here
                self.done[ss[done]] = True
                self.want[ss] = np.where(stuck & ~ends_here, 2 * zz, 0)
                ends = end[lo:lo + len(ss)]
                again = (nxt < 0) & ~done & ((self.pos[ss] < ends) | (stuck & ~ends_here))
                if again.any():
                    more.append(ss[again])
                if most is not None and n > most:  # (too many for one window: read less)
                    return None
            slots = np.concatenate(more) if more else np.zeros(0, np.int64)
        self._rechannel(sc.m[:n], sc.k[:n])
        return sc.t[:n], sc.m[:n], sc.k[:n]

    def _read_side(self, slots, t1, most):
        """_read on the reader's helper threads side by side (a dense part: one thread read Shinki's beat 1124 at a
        third of real time, 2026-10-27), the slots shared out by about how many bytes each still has before t1:
        a list of each part's (ticks, messages, tracks), slots in order; None = more than `most` in all."""
        helpers = self._helpers
        if len(slots) < 64 or not helpers:
            got = self._read(slots, t1, most)
            return None if got is None else [got]
        j2 = np.searchsorted(self.cp_key, slots * self.M + t1, "left") if len(self.cp_key) else None
        end = (np.where(j2 < self.seg1[slots], self.cp_off[np.minimum(j2, len(self.cp_off) - 1)], self.limit[slots])
               if j2 is not None else self.limit[slots])
        left = np.cumsum(np.maximum(end - self.pos[slots], 256))
        parts = len(helpers) + 1
        cuts = np.searchsorted(left, left[-1] * np.arange(1, parts) / parts)
        groups = [g for g in np.split(slots, cuts) if len(g)]
        jobs = [self._pool_x.submit(self._read, g, t1, most, None, sc) for g, sc in zip(groups[1:], helpers)]
        out = [self._read(groups[0], t1, most)] + [j.result() for j in jobs]
        if any(o is None for o in out) or sum(len(o[0]) for o in out) > most:
            return None
        return out

    def _rechannel(self, ms, ks):
        """Messages of tracks given a new channel in Track Properties (lm.rechannel, as playing started) moved onto
        it, in place (ks: each one's track number)."""
        new = self.chans
        if new is None or not len(ms):
            return
        ch = new[ks]
        hi = ms & 0xF0
        on = (ch >= 0) & (hi >= 0x80) & (hi < 0xF0)
        if on.any():
            ms[on] = (ms[on] & ~0xF) | ch[on].astype(ms.dtype)

    def _sorted(self, parts, w0, w1):
        """A window's events (parts: _read_side's list) in tick order, in arrays from the pool (the feeder gives
        them back once played). Several parts: counted and placed side by side on the helper threads."""
        n = sum(len(p[0]) for p in parts)
        with self._lock:
            i = next((i for i, b in enumerate(self._pool) if len(b[0]) >= n), None)
            got = None if i is None else self._pool.pop(i)  # (by place: remove() compares the arrays)
        if got is None:
            size = 1 << max(16, (n - 1).bit_length())
            got = (np.empty(size, np.int64), np.empty(size, np.int32), np.empty(size, np.int32))
        span = max(1, w1 - w0)
        if len(parts) == 1 or span > SIDE_SPAN:
            ts, ms, ks = parts[0] if len(parts) == 1 else (np.concatenate(a) for a in zip(*parts))
            _loops()[1](ts, ms, ks, w0, span, got[0], got[1], got[2])
            return got[0][:n], got[1][:n], got[2][:n], got
        count, place = _loops()[6], _loops()[7]
        h = np.zeros((len(parts), span), np.int64)
        list(self._pool_x.map(lambda j: count(parts[j][0], w0, h[j]), range(len(parts))))
        # (each part's place on each tick: every earlier tick's events, then the earlier parts' on this tick)
        tot = h.sum(0)
        start = np.cumsum(tot) - tot
        at = np.cumsum(h, 0) - h + start
        list(self._pool_x.map(lambda j: place(parts[j][0], parts[j][1], parts[j][2], w0, at[j], got[0], got[1],
                                              got[2]), range(len(parts))))
        return got[0][:n], got[1][:n], got[2][:n], got

    def _place(self, t0):
        """Every playable track's place at tick t0 (from its checkpoint before it, then read up to t0): (the skip
        counters to add: notes still sounding at t0, their offs left out; the controllers before t0 to send
        first)."""
        S = len(self.num)
        self.pos, self.tick, self.run = (np.zeros(S, np.int64) for _ in range(3))
        self.nxt, self.want = np.full(S, -1, np.int64), np.zeros(S, np.int64)
        self.done = np.zeros(S, bool)
        slots = np.arange(S, dtype=np.int64)
        opened = np.zeros(S * 2048, np.int64)  # (slot x channel x key: notes sounding)
        if len(self.cp_key):
            # (the last checkpoint BEFORE t0: one on t0 itself can sit amid that tick's events, the ones before it
            # were lost: Shinki's beat 340, ~500 note-offs a key, 2026-10-27)
            j = np.searchsorted(self.cp_key, slots * self.M + t0, "left") - 1
            has = (j >= self.seg0) & (j < self.seg1)
            jj = j[has]
            self.pos[has], self.tick[has], self.run[has] = self.cp_off[jj], self.cp_tick[jj], self.cp_run[jj]
            for s, local in zip(slots[has].tolist(), (jj - self.seg0[has]).tolist()):
                t = self.lm.tracks[int(self.num[s])]
                a, b = int(t.cp_ent[local]), int(t.cp_ent[local + 1])
                q = t.e_q[a:b].astype(np.int64)
                ok = q % 256 < 128
                np.add.at(opened, s * 2048 + (q[ok] // 256) * 128 + q[ok] % 256, t.e_cnt[a:b][ok])
                q, c = self.lm.hidden_open(int(self.num[s]), int(self.pos[s]))  # (extracted: not sounding)
                ok = q % 256 < 128
                np.add.at(opened, s * 2048 + (q[ok] // 256) * 128 + q[ok] % 256, -c[ok])
        lm = self.lm
        self.sk_i = np.array([lm.skip_at[n] + np.searchsorted(lm.skip_of(n), p) for n, p in
                              zip(self.num.tolist(), self.pos.tolist())], np.int64).reshape(-1)
        before = self._instruments(self.pos.copy(), self.tick.copy())
        # (notes only counted on the way: what's still open at t0 sounds on past it, each slot's own count; a
        # dense part's millions of notes are never kept, hunt 2026-10-25: 1.9 GB to start inside Shinki's wall)
        ts, ms, ks = self._read(slots, t0, count=opened)
        self._rechannel(before[1], before[2])
        if self.chans is not None:  # (notes sounding at t0 counted on their tracks' new channels: offs skipped there)
            live = np.flatnonzero(opened)
            ch = self.chans[self.num[live // 2048]]
            moved = live[ch >= 0]
            if len(moved):
                to = moved // 2048 * 2048 + ch[ch >= 0] * 128 + moved % 128
                got = opened[moved]
                opened[moved] = 0
                np.add.at(opened, to, got)
        if len(before[0]):
            ts, ms, ks = (np.concatenate([a, b]) for a, b in zip(before, (ts, ms, ks)))
        np.maximum(opened, 0, out=opened)
        skip = np.zeros(SKIP, np.int64)
        live = np.flatnonzero(opened)
        if len(live):
            port = self.tport[self.num[live // 2048]] % (16 if self.kind == "kestrel" else 64)  # (Kestrel: 16)
            np.add.at(skip, port * 2048 + live % 2048, opened[live])
        o = np.argsort(ts, kind="stable")
        return skip, (ts[o].copy(), ms[o].copy(), ks[o].copy())

    def _instruments(self, pos, tick):
        """The program changes (with their bank select) and bank selects in effect at each slot's checkpoint (pos /
        tick: where its reading starts; the ones after it are read with the rest): (ticks, messages, track
        numbers), sent before anything else when playing starts there. From the load pass's last ones per channel
        (midi_scan C_PTICK / C_PVAL / C_BANK / C_BTICK) when they came before the checkpoint, else the track walked
        up to it (midi_play.programs). The bank as it stands goes after the program (hunt 2026-10-25: a bank select
        after the last program change was lost, the next program change then took the wrong bank)."""
        PS = midi_play
        out = []
        for s in np.flatnonzero(pos > 0).tolist():
            n = int(self.num[s])
            c = self.lm.mf.tracks[n].counts
            plast, blast = c[C_PTICK:C_PTICK + 16] - 1, c[C_BTICK:C_BTICK + 16] - 1
            if not int(c[C_PROG]) and not (blast >= 0).any():
                continue
            if (plast >= tick[s]).any() or (blast >= tick[s]).any():  # (a change at / after the checkpoint: walked)
                st = self._walk_programs(s, int(pos[s]))
                plast, blast = st[PS.PS_PTICK:PS.PS_PTICK + 16] - 1, st[PS.PS_BTICK:PS.PS_BTICK + 16] - 1
                vals, banks = st[PS.PS_PVAL:PS.PS_PVAL + 16], st[PS.PS_BANK:PS.PS_BANK + 16]
            else:
                vals, banks = c[C_PVAL:C_PVAL + 16], c[C_BANK:C_BANK + 16]

            def bank_select(t, ch, bank):
                out.append((t, 0xB0 | ch | (bank & 127) << 16, n))
                out.append((t, 0xB0 | ch | 32 << 8 | (bank >> 7 & 127) << 16, n))
            for ch in range(16):
                if plast[ch] >= 0:
                    v, t = int(vals[ch]), int(plast[ch])
                    bank_select(t, ch, v >> 7)
                    out.append((t, 0xC0 | ch | (v & 127) << 8, n))
                if blast[ch] >= 0:
                    bank_select(int(blast[ch]), ch, int(banks[ch]))
        if not out:
            return np.zeros(0, np.int64), np.zeros(0, np.int32), np.zeros(0, np.int32)
        a = np.array(out, np.int64)
        return a[:, 0].copy(), a[:, 1].astype(np.int32), a[:, 2].astype(np.int32)

    def _walk_programs(self, s, upto):
        """midi_play.programs over slot s's bytes before byte place upto (its checkpoint), a piece at a time. Each
        place walked to is remembered with the file (hunt 2026-10-25: a giant track was read from its start at
        every Space and every jump, seconds from a hard disk): the next walk goes on from the nearest one before."""
        walk = _loops()[4]
        n = int(self.num[s])
        walks = self.lm.__dict__.setdefault("prog_walks", {}).setdefault(n, {})
        p = max((q for q in walks if q <= upto), default=0)
        st = walks[p].copy() if p else np.zeros(midi_play.PS_SIZE, np.int64)
        if p == upto:
            return st
        buf = np.empty(min(CHUNK, max(upto - p, 1)), np.uint8)
        while p < upto:
            z = min(len(buf), upto - p)
            if self.mode_file:
                self._file.seek(int(self.start_of[s]) + p)
                got = self._file.readinto(memoryview(buf)[:z])
                if got < z:
                    break
            else:
                buf[:z] = np.frombuffer(self.lm.tracks[n].data[p:p + z], np.uint8)
            used = walk(buf, 0, z, st)
            if used == 0:
                if len(buf) >= upto - p or len(buf) >= 1 << 28:  # (an event past the place / too big: done)
                    break
                buf = np.empty(2 * len(buf), np.uint8)
                continue
            p += used
        if p == upto:
            walks[p] = st.copy()
        return st

    def _reader(self, t0):
        from concurrent.futures import ThreadPoolExecutor
        path = self.lm.path if self.lm.mode != "memory" else None
        self._sc, self._helpers, self._pool_x = _Scratch(), [], None
        try:
            self._sc = _Scratch(path)
            self._file = self._sc.file
            self._helpers = [_Scratch(path) for _ in range(helper_count())]
            self._pool_x = ThreadPoolExecutor(max(1, len(self._helpers)), thread_name_prefix="loaded-read")
            self._table()
            start = t0
            while not self._stop.is_set():
                began = time.perf_counter()
                skip, chase = self._place(start)
                self._place_s = time.perf_counter() - began
                p = self._dense_part(start)
                if p is not None and p.t0 < start:  # (inside a part read ahead: its notes sounding there are left
                    skip = p.unheard(start)  # out as it counts them, the file's count would eat its own offs)
                with self._lock:
                    self._gen += 1
                    gen = self._gen
                    self._queue.clear()
                    self._queue.append([chase[0], chase[1], chase[2], 0, skip, None])  # (controllers before the start)
                    self._jump = None
                    self._unpart = None
                    # (read up to here now: the feeder measures from the new place, hunt 2026-10-25: a jump that
                    # took long to place asked for another at once, over and over, and playing went silent)
                    self._horizon = start
                span = max(1, self.lm.ppq // 8)
                w0 = start
                fresh = True  # (a part read ahead is taken part-way only where playing starts: its unheard offs
                # counted above; joined part-way later (done meanwhile, a track unmuted) = read live to its end)
                last = int(max((self.lm.tracks[int(n)].last_tick for n in self.num), default=0))
                unplaced = None  # (a part read ahead ended here: placed only when the reading goes live again)
                while not self._stop.is_set() and self._jump is None:
                    if self._unpart is not None:  # (a mute changed while a part played: live from where it was)
                        w0, span, fresh = self._leave_part(), max(1, self.lm.ppq // 8), False
                        unplaced = None
                        continue
                    if w0 > last:
                        self.ready.set()
                        time.sleep(0.02)
                        continue
                    if self._ahead(w0) > AHEAD_S or (self.player._clock is None and len(self._queue) > 8):
                        self.ready.set()  # (read far enough: the line may start)
                        time.sleep(0.005)
                        continue
                    w1 = w0 + span
                    tmap = self.player._clock[2] if self.player._clock is not None else self.player.map
                    if tmap is None:  # (started before the Player: its tempo comes a moment later)
                        time.sleep(0.005)
                        continue
                    if tmap.sec(w1) - tmap.sec(w0) > WINDOW_S:  # (never a long stretch at once: a quiet part's
                        # doubled windows ran into a wall and read for seconds)
                        w1 = max(w0 + 1, int(tmap.tick(tmap.sec(w0) + WINDOW_S)))
                    if self._queued() > QUEUE_MOST:  # (a wall read ahead: memory, never more than this)
                        self.ready.set()
                        time.sleep(0.005)
                        continue
                    p = self._dense_part(w0)
                    if p is not None and (p.t0 == w0 or fresh):  # (Kestrel: a part read ahead plays from there,
                        # the reading goes on past it)
                        fresh = False
                        with self._lock:
                            if self._gen != gen or self._jump is not None:
                                break
                            if self._unpart is not None:
                                continue
                            self._queue.append([p.ts, p.ms, p.tr, int(np.searchsorted(p.ts, w0 - p.t0, "left")),
                                                ("dense", p), None])
                            self._horizon = p.t1
                        # (its places only: the part's own offs and skips carry on; not between parts in a row, each
                        # placing read through the wall up to it: 0.3-1.2 s a part of ~0.1 s, the wall dropped,
                        # 2026-10-28)
                        unplaced = p.t1
                        self.dense_parts += 1
                        w0, span = p.t1, max(1, self.lm.ppq // 8)
                        continue
                    if unplaced is not None:
                        self._place(unplaced)
                        unplaced = None
                    cut = self._dense_start(w0, w1)
                    if cut is not None:
                        w1 = cut
                    began = time.perf_counter()
                    todo = np.flatnonzero(~self.done & (self.nxt < w1))
                    kept = [a.copy() for a in (self.pos, self.tick, self.run, self.nxt, self.want, self.done,
                                               self.sk_i)]
                    got = self._read_side(todo, w1, WINDOW_MOST)
                    if got is None:  # (too many events in it: a shorter window from the same place)
                        self.pos, self.tick, self.run, self.nxt, self.want, self.done, self.sk_i = kept
                        if w1 - w0 > 1:
                            span = max(1, (w1 - w0) // 4)
                            continue
                        # one tick holding more than a window may: stepped over, its notes only counted (never
                        # heard: no synth plays that many), every sound stopped there, on after it
                        skip, chase = self._place(w1)
                        with self._lock:
                            if self._gen != gen or self._jump is not None:
                                break
                            self._queue.append([chase[0], chase[1], chase[2], 0, ("wall", skip, w1), None])
                            self._horizon = w1
                        self.walls += 1
                        w0 = w1
                        continue
                    ts, ms, ks, base = self._sorted(got, w0, w1)
                    with self._lock:
                        if self._gen != gen or self._jump is not None:
                            break
                        self._queue.append([ts, ms, ks, 0, None, base])
                        self._horizon = w1
                    fresh = False
                    if tmap.sec(w1) - tmap.sec(start) >= READY_S:  # (the play line may start: the Player waits
                        self.ready.set()  # for it; a head start, else a dense part ran dry at once, hunt 10-25)
                    self.windows += 1
                    self.read_s += time.perf_counter() - began
                    # (the next window: about WANT events)
                    span = w1 - w0
                    if len(ts) > 2 * WANT:
                        span = max(1, span // 2)
                    elif len(ts) < WANT // 4 and span < SPAN_MOST // 2:
                        span *= 2
                    w0 = w1
                if self._jump is not None:
                    start = self._jump
                    self.jumps += 1
        except Exception as e:  # (a file gone / changed: Spiderweb's own shapes play on)
            self.error = e
            from files.errors import write_log
            import sys
            write_log(*sys.exc_info(), "(playing a loaded MIDI)")
        finally:
            self.ready.set()
            if self._pool_x is not None:
                self._pool_x.shutdown(wait=True)
            for sc in [self._sc] + self._helpers:
                sc.close()

    def _leave_part(self):
        """(The reader) the part read ahead the feeder stopped playing at tick tm (a track muted meanwhile: a part
        holds every track, hunt 2026-10-27): what was read past it dropped, the reading placed at tm, live from
        there. Nothing to leave out: the feeder's line holds every note before tm, copies too. Returns tm."""
        with self._lock:
            gen, p, tm = self._unpart
            self._queue.clear()
        self._place(tm)
        with self._lock:
            self._unpart = None
            if self._gen == gen:
                self._queue.append([np.zeros(0, np.int64), np.zeros(0, np.int32), np.zeros(0, np.int32), 0, None,
                                    None])
                self._horizon = tm
        return tm

    def _dense_now(self):
        """Kestrel (GPU): the loaded MIDI's read-ahead of its densest parts (files/play_dense.py) when it may be
        used now: as made for the file as it is, no track muted (it holds every track)."""
        d = self.lm.dense if self.kind == "kestrel" else None
        if d is None or self.muted.any():
            return None
        from files.play_dense import key_of
        return d if d.key == key_of(self.lm) else None

    def _dense_part(self, tick):
        d = self._dense_now()
        return None if d is None else d.part_at(tick)

    def _dense_start(self, t0, t1):
        d = self._dense_now()
        return None if d is None else d.start_in(t0, t1)

    def _queued(self):
        """Events read and not played yet."""
        with self._lock:
            return sum(len(e[0]) - e[3] for e in self._queue)

    def _ahead(self, tick):
        """Seconds tick is ahead of the play line now (None clock: 0)."""
        clock = self.player._clock
        if clock is None:
            return 0.0
        t, sec, tmap = clock
        return tmap.sec(tick) - (sec + time.perf_counter() - t)

    # --- the feeder ---------------------------------------------------------------------------------------------

    def _send(self, out, lens):
        if self.sinks:
            for r, live in enumerate(self.sinks):
                if lens[r]:
                    live.send_raw(out[r].ctypes.data, int(lens[r]))
        else:
            b = out[0, :lens[0]].astype(np.int64).reshape(-1, 3)
            send = self.out.send
            for m in (b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16).tolist():
                send(m)
        lens[:] = 0

    def silence(self, reset=False):
        """Every sound of the file stopped (sustain let go, all notes off: their release still rings); reset = the
        controllers back to where players start (before playing from a new place), the piano on every channel (the
        instruments in effect there come with the controllers: _place)."""
        msgs = [(0xB0, 64, 0), (0xB0, 123, 0)]
        if reset:
            msgs += [(0xB0, 121, 0), (0xB0, 7, 100), (0xB0, 10, 64), (0xE0, 0, 64), (0xB0, 0, 0), (0xB0, 32, 0),
                     (0xC0, 0)]
        if self.sinks:
            raw = bytes(b for ch in range(16) for m in msgs for b in (m[0] | ch,) + m[1:])
            buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
            for live in self.sinks:
                live.send_raw(ctypes.addressof(buf), len(raw))
        elif self.kind == "kestrel":  # (every port the file uses, in one push, now)
            kout = self.out.handle
            if kout is None:
                return
            ms = self._kestrel_silence(msgs)
            at = kout.frame_at(time.perf_counter())
            kout.k.push([at] * len(ms), ms, [0] * len(ms))
        else:
            for ch in range(16):
                for m in msgs:
                    self.out.send(m[0] | ch | m[1] << 8 | (m[2] if len(m) > 2 else 0) << 16)

    def _kestrel_silence(self, msgs=((0xB0, 64, 0), (0xB0, 123, 0))):
        """silence's messages for Kestrel: on every port the file uses (its port in the top byte)."""
        return [m[0] | ch | m[1] << 8 | (m[2] if len(m) > 2 else 0) << 16 | port << 24
                for port in range(min(self.ports, 16)) for ch in range(16) for m in msgs]

    def _feeder(self):
        hist, emit = _loops()[2], _loops()[3]
        K = self.synths
        out = np.zeros((K, OUT), np.uint8)
        lens = np.zeros(K, np.int64)
        h = np.zeros((K, 128), np.int64)
        line = midi_play.Line(places=SKIP)  # (each key's notes in file order: the offs of notes left out go too)
        thr, room = np.zeros(K, np.int64), np.zeros(K, np.int64)
        three = not self.sinks
        gen, cur = -1, None
        last = time.perf_counter()
        checked = 0.0  # (mutes read at once)
        hit = np.zeros(K, bool)  # (more notes than its budget lately: may get a bigger one)
        behind = np.zeros(K, bool)  # (backed up lately: never a bigger one then)
        if self.sinks:  # each synth's ceiling (learned last play), its place in its sound, checks without loss
            first = self.sinks[0]
            caps_key = (K, getattr(first, "voices", None), getattr(first, "font_paths", None))
            got = _caps.get(caps_key)
            cap = np.array(got if got is not None else [BUDGET_MOST] * K, np.float64)
            pos = [(time.perf_counter(), live.position()) for live in self.sinks]
            calm = np.zeros(K, np.int64)
        warm = None  # (when the line started: the synths' first moments lose a little sound, never counted)
        while not self._stop.is_set():
            time.sleep(STEP_S)
            clock = self.player._clock
            if clock is None:
                continue
            now = time.perf_counter()
            if warm is None:
                warm = now
            dt, last = min(now - last, 0.05), now
            if now - checked > 0.1:  # (mutes; the synths' load sets the budget)
                checked = now
                try:
                    m = np.zeros(len(self.muted), np.uint8)
                    for n in self.muted_of():
                        if 0 <= n < len(m):
                            m[n] = 1
                    self.muted = m
                except Exception:  # (the list changing meanwhile: next time)
                    pass
                if self.sinks:  # (each synth's own budget by the sound it lost and its load)
                    for r, live in enumerate(self.sinks):
                        t_at, p = time.perf_counter(), live.position()
                        lost = (t_at - pos[r][0]) - (p - pos[r][1]) / 8 / RATE  # (seconds it couldn't make)
                        pos[r] = (t_at, p)
                        if lost > LOST_S and now - warm > WARM_S and (hit[r] or behind[r]):  # (full of notes:
                            # its budget's doing; else a device / PC hiccup, nothing to learn)
                            cap[r] = max(BUDGET_LEAST, min(cap[r], self.budgets[r]) * LOST_CAP)
                            self.budgets[r] = max(BUDGET_LEAST, int(self.budgets[r] * 0.5))
                            calm[r] = 0
                        else:
                            calm[r] += 1
                            if calm[r] > 10:
                                cap[r] = min(BUDGET_MOST, cap[r] * CAP_RISE)
                        cpu = live.cpu()
                        if cpu > 150 or live.queued() > 2 * BACKLOG:  # (jammed: what waits is too late anyway)
                            live.drop_waiting()
                            self.budgets[r] = max(BUDGET_LEAST, int(self.budgets[r] * 0.25))
                            self.dropped += 1
                        elif cpu > 90:  # (past its time: far fewer, at once)
                            self.budgets[r] = max(BUDGET_LEAST, int(self.budgets[r] * 0.5))
                        elif cpu > 75:
                            self.budgets[r] = max(BUDGET_LEAST, int(self.budgets[r] * 0.75))
                        elif cpu < 40 and hit[r] and not behind[r] and calm[r] > 3:
                            self.budgets[r] = int(self.budgets[r] * GROW)
                        self.budgets[r] = max(BUDGET_LEAST, min(self.budgets[r], int(cap[r])))
                    _caps[caps_key] = cap.tolist()
                    hit[:] = False
                    behind[:] = False
            t_c, sec, tmap = clock
            s_now = sec + now - t_c + (GROUP_BUFFER if self.sinks else 0.0)  # (the group's sound comes out later)
            T = tmap.tick(s_now)
            t_late = int(tmap.tick(s_now - LATE_S))
            with self._lock:
                if self._gen != gen:  # (read again from a new place: what was sounding stopped)
                    if gen >= 0:
                        self.silence(reset=True)
                    gen, cur = self._gen, None
                    line.reset()
                if cur is None and self._queue:
                    cur = self._queue.popleft()
                    self._took(cur, line)
                horizon = getattr(self, "_horizon", None)
            if cur is None:
                if horizon is not None and tmap.sec(horizon) < s_now - BEHIND_S and self._jump is None:
                    self._jump = int(tmap.tick(s_now + JUMP_S + self._place_s)) + 1
                continue
            step = np.maximum(1, (self.budgets * dt).astype(np.int64))  # (note-ons each synth may take now)
            if self.sinks:  # (a synth still working through what it was sent: no new notes, never a wait)
                for r, live in enumerate(self.sinks):
                    if live.queued() > BACKLOG:
                        step[r] = 0
                        behind[r] = True
            while cur is not None:
                ts, ms, ks, i = cur[0], cur[1], cur[2], cur[3]
                i1 = int(np.searchsorted(ts, T, "right"))
                if i1 > i:
                    h[:] = 0
                    hist(ms, ks, i, i1, ts, t_late, self.muted, self.tport, self.ports, K, h)
                    above = np.cumsum(h[:, ::-1], axis=1)[:, ::-1]  # (note-ons at or above each velocity)
                    for r in range(K):
                        budget = int(step[r])
                        fits = above[r] <= budget
                        if fits[1]:
                            thr[r], room[r] = 0, 0
                        elif not fits.any():  # (more of the loudest than fit: as many of them as fit)
                            hit[r] = True
                            thr[r], room[r] = 128, budget
                        else:
                            hit[r] = True
                            v = int(np.argmax(fits))  # (the lowest velocity whose ons all fit; one below: the rest)
                            thr[r], room[r] = v, budget - int(above[r, v])
                    ons = int(h.sum())
                    line.room(midi_play.ons_in(ms, i, i1))
                    while i < i1:
                        j = emit(ms, ks, i, i1, ts, t_late, self.muted, self.tport, self.ports, K, thr, room,
                                 out, lens, three, *line.arrays())
                        self._send(out, lens)
                        i = j
                    cur[3] = i
                    kept = np.minimum(above[:, 1], step)
                    step -= kept  # (the next window's events in this step share what's left)
                    self.sent += int(kept.sum())
                    self.thinned += max(0, ons - int(kept.sum()))
                if i >= len(ts):
                    with self._lock:
                        if cur[5] is not None and len(self._pool) < POOL and len(cur[5][0]) <= POOL_LONGEST:
                            self._pool.append(cur[5])  # (its arrays: for a later window; a wall's go)
                        cur = self._queue.popleft() if self._queue and self._gen == gen else None
                        if cur is not None:
                            self._took(cur, line)
                    continue
                break
        self.silence()

    def _feeder_kestrel(self):
        """Kestrel (GPU)'s feeder: no budget here (Kestrel's own chooses, fair by channel + port, midi_play.stamp), the
        events stamped with the frame each sounds at and pushed PUSH_S ahead of the line in whole blocks (a busy
        block's note-ons ranked together; its rest waits for the next push). Note lengths are paired inside a push
        (a note ending later counts as long)."""
        from files.kestrel import RATE as K_RATE
        stamp = _loops()[5]
        kout = self.out.handle
        BLOCK = kout.k.block  # (the block size Kestrel opened with: Settings → Sound, hunt 2026-10-28)
        line = midi_play.Line()  # (each key's notes in Kestrel's order: the offs of notes left out go too)
        counts = np.zeros(3, np.int64)
        table = (np.zeros(midi_play.COPY_TABLE, np.uint32), np.zeros(midi_play.COPY_TABLE, np.int64),
                 np.zeros(midi_play.COPY_TABLE, np.int64))  # (stamp's: exact copies on one tick sent once)
        tm_of = tm = None
        song, told = 0, None
        base = align = 0.0
        ahead = []  # (frames, msgs, tracks, copies) pushed and maybe not played yet: a tempo change pushes them again
        gen, cur = -1, None
        checked = 0.0
        held = None  # (frames, msgs, tracks) of a block not whole yet
        # a wall stepped over: its silence goes IN ORDER, at the wall's own frame after what came before it (sent at
        # once, notes starting just before it came after it and their offs were left out: they rang on, hunt
        # 2026-10-27); the controllers sent again with it not before it (floor)
        wall = {"t": None, "floor": 0.0}
        silent = np.array(self._kestrel_silence(), np.uint32)

        def took(cur):
            """A window taken: a wall stepped over = every sound stopped, the notes sounding after it left out;
            notes sounding where playing starts (the reader's counts) left out."""
            if isinstance(cur[4], tuple) and cur[4][0] == "wall":
                line.reset(cur[4][1])
                wall["t"] = cur[4][2]
            elif cur[4] is not None and not isinstance(cur[4], tuple):
                line.add(cur[4])
        while not self._stop.is_set():
            time.sleep(STEP_S)
            clock = self.player._clock
            if clock is None:
                continue
            now = time.perf_counter()
            if now - checked > 0.1:
                checked = now
                was = self.muted
                self._read_mutes()
                # (a part read ahead holds every track: a mute changing while it plays = it stops where it is,
                # the rest read live with the mutes, hunt 2026-10-27: heard to the part's end, seconds)
                if cur is not None and isinstance(cur[4], tuple) and cur[4][0] == "dense" and \
                        not np.array_equal(was, self.muted):
                    p = cur[4][1]
                    with self._lock:
                        if self._gen == gen and self._unpart is None:
                            self._unpart = (gen, p, p.t0 + int(cur[0][cur[3]]) if cur[3] < len(cur[0]) else p.t1)
                            cur = None
            t_c, sec, tmap = clock
            s_now = sec + now - t_c
            T = tmap.tick(s_now + PUSH_S)
            edge = kout.frame_at(now + PUSH_S) // BLOCK * BLOCK  # (whole blocks only)
            # (a note-on for sound already made, or about to be, would all pile into one block: left out)
            least = float(kout.k.stats()["rendered"] + BLOCK)
            with self._lock:
                if self._gen != gen:  # (read again from a new place: what was sent ahead goes, sound stopped)
                    if gen >= 0:
                        kout.cancel()
                        self.silence(reset=True)
                    gen, cur, held = self._gen, None, None
                    line.reset()
                    wall["t"], wall["floor"] = None, 0.0
                    ahead.clear()
                    tm_of = None
                    # (the song's start on a block edge, < 5.3 ms later or sooner: each play puts the same notes in
                    # the same blocks, so Kestrel's remembered limits keep the same ones, F3 2026-10-28)
                    raw = kout._f0 + (t_c - sec - kout._t0) * K_RATE
                    align = self.k_align = round(raw / BLOCK) * BLOCK - raw
                if cur is None and self._queue and self._unpart is None:
                    cur = self._queue.popleft()
                    took(cur)
                horizon = getattr(self, "_horizon", None)
            if tm_of is not None and tmap is not tm_of:  # (the tempo changed while playing: see _retime)
                new = _tm_arrays(tmap)
                if not all(np.array_equal(a, b) for a, b in zip(tm, new)):
                    base_new = kout._f0 + (t_c - sec - kout._t0) * K_RATE + align
                    held, base = self._retime(kout, ahead, held, tm, base, new, base_new)
                    if base != base_new:  # (the clock moved so the line, own shapes and these notes stay together)
                        clock = self.player._clock
                        if clock is not None and clock[2] is tmap:
                            self.player._clock = clock = (clock[0] + (base - base_new) / K_RATE, clock[1], clock[2])
                            t_c = clock[0]
                tm_of, tm = tmap, new
            if cur is None:
                self.prof[3] += 1
                if horizon is not None:
                    self.prof[4] = s_now + PUSH_S - tmap.sec(horizon)  # (seconds the reading is short of the push)
            if cur is None and horizon is not None and tmap.sec(horizon) < s_now - BEHIND_S and self._jump is None:
                self._jump = int(tmap.tick(s_now + JUMP_S + self._place_s)) + 1
            parts = [] if held is None else [held]
            while cur is not None:
                if wall["t"] is not None:
                    f_w = max(least, kout._f0 + (t_c - sec - kout._t0) * K_RATE + align + tmap.sec(wall["t"]) * K_RATE)
                    wall["t"], wall["floor"] = None, f_w
                    n = len(silent)
                    parts.append((np.full(n, int(f_w + 0.5), np.uint64), silent, np.zeros(n, np.uint16),
                                  np.ones(n, np.uint32)))
                ts, ms, ks, i = cur[0], cur[1], cur[2], cur[3]
                dense = cur[4][1] if isinstance(cur[4], tuple) and cur[4][0] == "dense" else None
                i1 = int(np.searchsorted(ts, T, "right") if dense is None else
                         np.searchsorted(ts, T - dense.t0, "right"))
                if i1 > i:
                    if tm_of is not tmap:  # (the tempo map as arrays, for the compiled loop)
                        tm_of, tm = tmap, _tm_arrays(tmap)
                    size = i1 - i if dense is None else midi_play.pre_room(ms, dense.cp, i, i1, line.st)
                    line.room(midi_play.ons_in(ms, i, i1) if dense is None else midi_play.pre_nodes(ms, i, i1))
                    fr = np.empty(size, np.uint64)
                    m = np.empty(size, np.uint32)
                    tr = np.empty(size, np.uint16)
                    cp = np.empty(size, np.uint32)
                    counts[:2] = 0  # (counts[2]: the copy table's generation, kept)
                    base = kout._f0 + (t_c - sec - kout._t0) * K_RATE + align
                    song = self._song(tm)
                    if told != (song, round(base)):
                        told = (song, round(base))
                        kout.k.set_song(base, song)
                    t_s = time.perf_counter()
                    if dense is None:
                        n = stamp(ms, ks, i, i1, ts, self.muted, self.tport, *tm, base, float(K_RATE), least,
                                  fr, m, tr, counts, cp, *table, *line.arrays())
                    else:  # (read ahead: copies collapsed already)
                        n = _loops()[9](ts, ms, ks, dense.cp, dense.t0, i, i1, *tm, base, float(K_RATE),
                                        least, fr, m, tr, cp, counts, False, *line.arrays())
                    self.prof[0] += time.perf_counter() - t_s
                    if wall["floor"]:  # (a wall's controllers: after its silence, never before it)
                        np.maximum(fr[:n], np.uint64(int(wall["floor"] + 0.5)), out=fr[:n])
                    parts.append((fr[:n], m[:n], tr[:n], cp[:n]))
                    self.sent += int(counts[1])
                    self.thinned += int(counts[0] - counts[1])  # (muted / too late: as _feeder counts them)
                    cur[3] = i = i1
                if i >= len(ts):
                    with self._lock:
                        if cur[5] is not None and len(self._pool) < POOL and len(cur[5][0]) <= POOL_LONGEST:
                            self._pool.append(cur[5])
                        cur = self._queue.popleft() if self._queue and self._gen == gen and self._unpart is None else None
                        wall["floor"] = 0.0
                        if cur is not None:
                            took(cur)
                    continue
                break
            if not parts:
                continue
            fr, m, tr, cp = (np.concatenate(a) for a in zip(*parts)) if len(parts) > 1 else parts[0]
            if cur is None and len(fr):  # (read only up to its last window: that window's last block waits too,
                last = int(fr[-1]) // BLOCK * BLOCK  # else it went half now, half in the next push)
                # ... but only while the reading may still add to it: read past its end (a gap in the song) or
                # about to be made = it goes now (user 2026-10-27, Septette bar 52: the offs before a 1.6 s
                # rest waited for the next note, 0.68 s late, the chord rang on)
                read_to = None if horizon is None else (kout._f0 + (t_c - sec - kout._t0) * K_RATE + align
                                                        + tmap.sec(horizon) * K_RATE)
                if (read_to is None or read_to < last + BLOCK) and last > least + HOLD_BLOCKS * BLOCK:
                    edge = min(edge, last)
            cut = int(np.searchsorted(fr, edge))  # (the block still being filled waits)
            held = (fr[cut:], m[cut:], tr[cut:], cp[cut:]) if cut < len(fr) else None
            ahead[:] = [c for c in ahead if int(c[0][-1]) >= least]  # (played: gone)
            # (in pieces of PIECE_BLOCKS, earliest first: one push of 0.4 s of a dense part took so long to rank
            # that its first blocks were made before it came, all of it then in one block)
            a = 0
            while a < cut:
                b = min(cut, int(np.searchsorted(fr, (int(fr[a]) // BLOCK + PIECE_BLOCKS) * BLOCK)))
                t_p = time.perf_counter()
                kout.k.push_copies(fr[a:b], m[a:b], tr[a:b], cp[a:b])
                ahead.append((fr[a:b], m[a:b], tr[a:b], cp[a:b]))
                self.prof[1] += time.perf_counter() - t_p
                self.prof[2] += 1
                a = b
        kout.cancel()
        kout.k.set_song(0.0, 0)  # (own shapes alone next: no place in a song)
        self.silence()

    def _feeding(self, feeder):
        """A feeder's thread: a failure stops playing with the reader's message (it went silent while the play line
        ran on, only errors.log knew: hunt 2026-10-28)."""
        try:
            feeder()
        except Exception as e:
            self.error = e
            from files.errors import write_log
            import sys
            write_log(*sys.exc_info(), "(playing a loaded MIDI)")

    def _song(self, tm):
        """Kestrel's song number: its note limits remembered by place are kept only while it stays the same. This
        loaded MIDI (its own number: a memory address came back for a later MIDI, hunt 2026-10-28) at this tempo,
        with these mutes, hidden / removed / rechannelled notes and own shapes (soloing after a full play kept the
        full play's low limits, same hunt)."""
        global _SONGS
        no = getattr(self.lm, "_kb_no", None)
        if no is None:
            _SONGS += 1
            no = self.lm._kb_no = _SONGS
        from files.play_dense import key_of
        return hash((no, len(tm[0]), float(tm[0].sum()), float(tm[2].sum()), self.muted.tobytes(), key_of(self.lm),
                     getattr(self.player, "notes_ver", 0))) | 1

    @staticmethod
    def _retime(kout, ahead, held, tm, base, new, base_new):
        """The tempo changed while playing (Kestrel): what was pushed ahead (up to PUSH_S, at the old speed) taken
        back from a couple of blocks on and pushed again at the new speed, with the block not pushed yet (held);
        returns (the new held, the song's start frame from now on). Before (F8, 2026-10-28) up to a second played on
        at the old speed, then the rest came at the new one, out of step with the line.
        The new tempo starts EXACTLY where the sound made at the old one ends (frame `at`): the song's start frame is
        set so, not by the clock (the hunt, 2026-10-28: by the clock, a big speed-up had every note of the ~80 ms
        between the line and `at` start at `at` at once); the caller moves the clock by the difference."""
        from files.kestrel import RATE as K_RATE
        BLOCK = kout.k.block
        at = kout.k.cut((kout.k.stats()["rendered"] // BLOCK + 2) * BLOCK)
        if at is None:  # (no answer: it plays on as it was)
            return held, base_new

        def tick_of(f):  # (frame -> tick at the old tempo)
            ticks, secs, spt = tm
            s = (np.asarray(f, np.float64) - base) / K_RATE
            i = np.maximum(np.searchsorted(secs, s, "right") - 1, 0)
            return ticks[i] + (s - secs[i]) / spt[i]

        def sec_new(t):  # (tick -> song second at the new tempo)
            ticks, secs, spt = new
            j = np.maximum(np.searchsorted(ticks, t, "right") - 1, 0)
            return secs[j] + (t - ticks[j]) * spt[j]
        anchor = at - float(sec_new(tick_of([at]))[0]) * K_RATE
        got = []
        for c in ahead + ([held] if held is not None else []):
            k = int(np.searchsorted(c[0], at))
            if k < len(c[0]):
                got.append(tuple(a[k:] for a in c))
        ahead.clear()
        if not got:
            return None, anchor
        fr, m, tr, cp = (np.concatenate(a) for a in zip(*got))
        fr = np.maximum(np.round(anchor + sec_new(tick_of(fr)) * K_RATE), at).astype(np.uint64)
        o = np.argsort(fr, kind="stable")  # (in order again: the block not pushed yet came after the rest)
        return (fr[o], m[o], tr[o], cp[o]), anchor

    def _read_mutes(self):
        try:
            m = np.zeros(len(self.muted), np.uint8)
            for n in self.muted_of():
                if 0 <= n < len(m):
                    m[n] = 1
            self.muted = m
        except Exception:  # (the list changing meanwhile: next time)
            pass

    def _took(self, cur, line):
        """A window taken by the feeder: the notes left out at its start (their note-offs too) at the back of their
        keys' lines; a wall stepped over (the reader's ("wall", counts)): every sound stopped, the lines = the notes
        sounding after it, all left out."""
        if cur[4] is None or isinstance(cur[4], tuple) and cur[4][0] == "dense":  # (a part read ahead: its own
            # skips come at its end)
            return
        if isinstance(cur[4], tuple):
            self.silence()
            line.reset(cur[4][1])
        else:
            line.add(cur[4])

    # --- start / stop -------------------------------------------------------------------------------------------

    def start(self, t0):
        """Plays from tick t0 (the Player started there; its clock leads)."""
        self.stop()
        self._stop.clear()
        self.ready.clear()
        self._horizon = None
        self._unpart = None
        self.silence(reset=True)
        feeder = self._feeder_kestrel if self.kind == "kestrel" else self._feeder
        self.threads = [threading.Thread(target=self._reader, args=(int(t0),), daemon=True),
                        threading.Thread(target=self._feeding, args=(feeder,), daemon=True)]
        for th in self.threads:
            th.start()

    def stop(self):
        self._stop.set()
        for th in self.threads:
            th.join()
        self.threads = []
        with self._lock:
            self._queue.clear()
