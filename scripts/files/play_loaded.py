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
from files.midi_scan import C_BEND, C_CC, C_OFF, C_ON, C_ON0, C_POLY, C_PRESS

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
# what it was; no loss for a while = the ceiling creeps up (CAP_RISE each check). Kept for the next play (_caps).
LOST_S = 0.005  # sound missing in one check (~0.1 s) that counts as lost
LOST_CAP = 0.7
CAP_RISE = 1.01  # (~10 % a second)
GROW = 1.08  # a budget growing: this much each check
WARM_S = 2.0  # sound lost this soon after the line starts never lowers a ceiling (start-up loses some at any load)
_caps = {}  # (synths, voices each) -> each synth's learned ceiling, for the next play
SKIP = 64 * 16 * 128  # (port x channel x key: note-offs still to leave out)
POOL = 12  # windows' arrays kept for use again at most


def _loops():
    fast = speed.loops()
    if fast is not None:
        return fast.play_read, fast.play_order, fast.play_hist, fast.play_emit
    return midi_play.read, midi_play.order, midi_play.hist, midi_play.emit


def track_ports(lm):
    """(port index per track (0 .. ports - 1, by the file's FF 21 port numbers; none = port 0), ports used)."""
    from notes.loaded import PORT
    raw = []
    for t in lm.tracks:
        got = t.metas(PORT)
        raw.append(got[0][1][0] if got and got[0][1] else 0)
    used = sorted(set(raw)) or [0]
    return np.array([used.index(p) for p in raw], np.int64), len(used)


def playable(mt):
    """A track with channel events to play (midi_scan's counts)."""
    c = mt.counts
    return c is not None and int(c[C_ON] + c[C_OFF] + c[C_ON0] + c[C_POLY] + c[C_CC] + c[C_PRESS] + c[C_BEND]) > 0


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
        if sinks:
            self.tport, self.ports = track_ports(lm)
        else:  # (one device: 16 channels, every port onto them)
            self.tport, self.ports = np.zeros(len(lm.tracks), np.int64), 1
        self.muted = np.zeros(len(lm.tracks), np.uint8)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._queue = deque()  # [ticks, messages, tracks, place reached, skips to add, arrays] windows read, in order
        self._pool = []  # (arrays of windows played: used again, never made anew each window)
        self._gen = 0  # (a jump: windows read before it are thrown away)
        self._jump = None  # tick the reader starts again from (asked by the feeder)
        self.thinned = 0  # note-ons left out (budget / late), so far
        self.sent = 0  # note-ons sent
        self.jumps = 0
        self.dropped = 0  # (times a jammed synth's waiting messages were thrown away)
        self.windows, self.read_s = 0, 0.0  # (windows read, seconds reading them took: measuring)
        self.error = None
        self.threads = []
        self.ready = threading.Event()  # (set once the first notes are read: Player.ready, the line waits for it)
        self._place_s = 0.0  # (seconds finding every track's place took last time: a jump goes that much further)

    # --- the reader ---------------------------------------------------------------------------------------------

    def _table(self):
        """Every playable track's arrays, one place per track ("slot"): number, bytes, checkpoints put together
        (cp_key = slot x M + tick, so one search finds each track's own)."""
        lm = self.lm
        num = [n for n, (t, mt) in enumerate(zip(lm.tracks, lm.mf.tracks)) if playable(mt) and t.limit > 0]
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

    def _fill(self, buf, slots, at, size):
        """buf[at[j]:at[j] + size[j]] = slot j's bytes from its place (file: one read each; kept bytes: copied)."""
        if self.mode_file:
            f = self._file
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

    def _read(self, slots, t1):
        """Slots' events up to tick t1 (their places moved on): (ticks, messages, track numbers), track by track."""
        read = _loops()[0]
        n = 0
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
                cut = np.searchsorted(np.cumsum(zz), BATCH, "right") + 1  # (about BATCH bytes a call)
                ss, zz = ss[:cut], zz[:cut]
                if cut < len(slots[lo:lo + 4096]):
                    more.append(slots[lo:lo + 4096][cut:])
                at = np.concatenate([[0], np.cumsum(zz)[:-1]]).astype(np.int64)
                total = int(zz.sum())
                if self._buf is None or len(self._buf) < total:
                    self._buf = np.empty(max(total, 1 << 22), np.uint8)
                self._fill(self._buf, ss, at, zz)
                tick, run = self.tick[ss].copy(), self.run[ss].copy()
                nxt, used = np.empty(len(ss), np.int64), np.empty(len(ss), np.int64)
                n = self._room(n, total // 2 + 2 * len(ss))
                n = read(self._buf, at, zz, t1, tick, run, nxt, used, self.num32[ss], self._sc_t, self._sc_m,
                         self._sc_k, n)
                self.tick[ss], self.run[ss], self.nxt[ss] = tick, run, nxt
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
            slots = np.concatenate(more) if more else np.zeros(0, np.int64)
        return self._sc_t[:n], self._sc_m[:n], self._sc_k[:n]

    def _room(self, n, more):
        """The reading's own arrays hold n events + more (grown, never made anew for each window: fresh memory
        cost more than the reading itself)."""
        if n + more > len(self._sc_t):
            size = max(n + more, 2 * len(self._sc_t), 1 << 20)

            def grow(a, kind):
                return np.concatenate([a[:n], np.empty(size - n, kind)])
            self._sc_t, self._sc_m, self._sc_k = (grow(self._sc_t, np.int64), grow(self._sc_m, np.int32),
                                                  grow(self._sc_k, np.int32))
        return n

    def _sorted(self, ts, ms, ks, w0, w1):
        """A window's events in tick order, in arrays from the pool (the feeder gives them back once played)."""
        n = len(ts)
        with self._lock:
            i = next((i for i, b in enumerate(self._pool) if len(b[0]) >= n), None)
            got = None if i is None else self._pool.pop(i)  # (by place: remove() compares the arrays)
        if got is None:
            size = 1 << max(16, (n - 1).bit_length())
            got = (np.empty(size, np.int64), np.empty(size, np.int32), np.empty(size, np.int32))
        _loops()[1](ts, ms, ks, w0, max(1, w1 - w0), got[0], got[1], got[2])
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
            j = np.searchsorted(self.cp_key, slots * self.M + t0, "right") - 1
            has = (j >= self.seg0) & (j < self.seg1)
            jj = j[has]
            self.pos[has], self.tick[has], self.run[has] = self.cp_off[jj], self.cp_tick[jj], self.cp_run[jj]
            for s, local in zip(slots[has].tolist(), (jj - self.seg0[has]).tolist()):
                t = self.lm.tracks[int(self.num[s])]
                a, b = int(t.cp_ent[local]), int(t.cp_ent[local + 1])
                q = t.e_q[a:b].astype(np.int64)
                ok = q % 256 < 128
                np.add.at(opened, s * 2048 + (q[ok] // 256) * 128 + q[ok] % 256, t.e_cnt[a:b][ok])
        ts, ms, ks = self._read(slots, t0)
        notes = (ms & 0xE0) == 0x80
        if notes.any():  # (what's still open at t0 sounds on past it; each slot's own count)
            slot_of = np.searchsorted(self.num, ks[notes].astype(np.int64))
            m = ms[notes].astype(np.int64)
            q = slot_of * 2048 + (m & 15) * 128 + ((m >> 8) & 127)
            np.add.at(opened, q, np.where(((m & 0xF0) == 0x90) & (((m >> 16) & 127) > 0), 1, -1))
        np.maximum(opened, 0, out=opened)
        skip = np.zeros(SKIP, np.int64)
        live = np.flatnonzero(opened)
        if len(live):
            port = self.tport[self.num[live // 2048]] % 64
            np.add.at(skip, port * 2048 + live % 2048, opened[live])
        other = ~notes & ((ms & 0xF0) != 0xA0)
        o = np.argsort(ts[other], kind="stable")
        return skip, (ts[other][o], ms[other][o], ks[other][o])

    def _reader(self, t0):
        self._buf, self._file = None, None
        self._sc_t, self._sc_m, self._sc_k = np.empty(0, np.int64), np.empty(0, np.int32), np.empty(0, np.int32)
        try:
            if self.lm.mode != "memory":
                self._file = open(self.lm.path, "rb")
            self._table()
            start = t0
            while not self._stop.is_set():
                began = time.perf_counter()
                skip, chase = self._place(start)
                self._place_s = time.perf_counter() - began
                with self._lock:
                    self._gen += 1
                    gen = self._gen
                    self._queue.clear()
                    self._queue.append([chase[0], chase[1], chase[2], 0, skip, None])  # (controllers before the start)
                    self._jump = None
                span = max(1, self.lm.ppq // 8)
                w0 = start
                last = int(max((self.lm.tracks[int(n)].last_tick for n in self.num), default=0))
                while not self._stop.is_set() and self._jump is None:
                    if w0 > last:
                        self.ready.set()
                        time.sleep(0.02)
                        continue
                    if self._ahead(w0) > AHEAD_S or (self.player._clock is None and len(self._queue) > 8):
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
                    began = time.perf_counter()
                    todo = np.flatnonzero(~self.done & (self.nxt < w1))
                    ts, ms, ks = self._read(todo, w1)
                    ts, ms, ks, base = self._sorted(ts, ms, ks, w0, w1)
                    with self._lock:
                        if self._gen != gen or self._jump is not None:
                            break
                        self._queue.append([ts, ms, ks, 0, None, base])
                        self._horizon = w1
                    self.ready.set()  # (the play line may start: the Player waits for it)
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
            if self._file is not None:
                self._file.close()

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
        controllers back to where players start (before playing from a new place)."""
        msgs = [(0xB0, 64, 0), (0xB0, 123, 0)]
        if reset:
            msgs += [(0xB0, 121, 0), (0xB0, 7, 100), (0xB0, 10, 64), (0xE0, 0, 64)]
        raw = bytes(b for ch in range(16) for s, a, v in msgs for b in (s | ch, a, v))
        if self.sinks:
            buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
            for live in self.sinks:
                live.send_raw(ctypes.addressof(buf), len(raw))
        else:
            for i in range(0, len(raw), 3):
                self.out.send(raw[i] | raw[i + 1] << 8 | raw[i + 2] << 16)

    def _feeder(self):
        hist, emit = _loops()[2], _loops()[3]
        K = self.synths
        out = np.zeros((K, OUT), np.uint8)
        lens = np.zeros(K, np.int64)
        h = np.zeros((K, 128), np.int64)
        skip = np.zeros(SKIP, np.int64)
        thr, room = np.zeros(K, np.int64), np.zeros(K, np.int64)
        three = not self.sinks
        gen, cur = -1, None
        last = time.perf_counter()
        checked = 0.0  # (mutes read at once)
        hit = np.zeros(K, bool)  # (more notes than its budget lately: may get a bigger one)
        behind = np.zeros(K, bool)  # (backed up lately: never a bigger one then)
        if self.sinks:  # each synth's ceiling (learned last play), its place in its sound, checks without loss
            got = _caps.get(K)
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
                        if lost > LOST_S and now - warm > WARM_S:
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
                    _caps[K] = cap.tolist()
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
                    skip[:] = 0
                if cur is None and self._queue:
                    cur = self._queue.popleft()
                    if cur[4] is not None:
                        skip += cur[4]
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
                    while i < i1:
                        j = emit(ms, ks, i, i1, ts, t_late, self.muted, self.tport, self.ports, K, thr, room, skip,
                                 out, lens, three)
                        self._send(out, lens)
                        i = j
                    cur[3] = i
                    kept = np.minimum(above[:, 1], step)
                    step -= kept  # (the next window's events in this step share what's left)
                    self.sent += int(kept.sum())
                    self.thinned += max(0, ons - int(kept.sum()))
                if i >= len(ts):
                    with self._lock:
                        if cur[5] is not None and len(self._pool) < POOL:  # (its arrays: for a later window)
                            self._pool.append(cur[5])
                        cur = self._queue.popleft() if self._queue and self._gen == gen else None
                        if cur is not None and cur[4] is not None:
                            skip += cur[4]
                    continue
                break
        self.silence()

    # --- start / stop -------------------------------------------------------------------------------------------

    def start(self, t0):
        """Plays from tick t0 (the Player started there; its clock leads)."""
        self.stop()
        self._stop.clear()
        self.ready.clear()
        self._horizon = None
        self.silence(reset=True)
        self.threads = [threading.Thread(target=self._reader, args=(int(t0),), daemon=True),
                        threading.Thread(target=self._feeder, daemon=True)]
        for th in self.threads:
            th.start()

    def stop(self):
        self._stop.set()
        for th in self.threads:
            th.join()
        self.threads = []
        with self._lock:
            self._queue.clear()
