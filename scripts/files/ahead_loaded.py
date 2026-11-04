"""Rendered ahead with a LOADED MIDI (PLAN.md L1): LoadedSong = what the pieces are made of, the loaded MIDI + your
own shapes (an ahead_pieces.Song) on the same clock (the tempo as heard). A piece of it is a small MIDI file:
- the heard tempo (a track of its own; the file's own tempo events inside the copied bytes are made text),
- each loaded track that plays (not muted / removed, with events there): its port (as live play numbers them), the
  controllers / instruments / bends in force at the piece's early start (the CC events kept in memory), the notes
  sounding there struck where they began (found back over the checkpoints, at most HELD_PRE early), then its own
  bytes COPIED as they are to the piece's end + POST_S (files/midi_piece.py piece_track),
- your shapes' notes + CC lines (Song.tracks), channel 10 melodic only on ports the loaded MIDI doesn't use.
Measured (dev/scratch/loaded_piece_bytes.py + lp_compare.py): the same sound as Merlin's render of the whole file
over that stretch (-113..-164 dB), Shinki 4.5 s of music made in ~2 s."""

import numpy as np

from files import midi_piece
from files.ahead_pieces import HELD_PRE, POST_S, PRE, Song
from files.synth import RATE, SynthError


FAR = midi_piece.FAR
PRE_MOST = 64 << 20  # bytes the early start (PRE) writes at most: shorter in dense walls (Shinki 440 s: 0.5 s) ...
PRE_STEPS = (PRE, 0.5, 0.25, 0.1, 0.05, 0.02)  # ... trying these seconds (the last one goes whatever it costs)
SPAN_LEAST = RATE // 20  # (50 ms: a piece is never cut shorter, however dense: each part reads ~320 MB again on
# a 2,788-track MIDI)
HELD_EXACT_MOST = 1 << 24  # held copies a piece strikes EACH AT ITS OWN NOTE-ON (PLAN B1, 2026-11-04: all of a key's
# copies struck at one tick = a burst of that key at block edges, +15..+27 dB, Shinki 442 s); over it (Shinki's end
# holds 788 M copies = 12 GB of starts) the rest go the old way: one tick a key


def _loop():
    from files import speed
    fast = speed.loops()
    return fast.piece_track if fast is not None else midi_piece.piece_track


def _held_loops():
    from files import speed
    fast = speed.loops()
    if fast is not None:
        return fast.piece_held_ons, fast.piece_held_head
    return midi_piece.held_ons, midi_piece.held_head


class LoadedSong:
    """lm = the LoadedMidi; own = a Song of your shapes (its tempo = the heard tempo, ticks = the file's, the
    project's PPQ is the file's); muted = TrackList.loaded_mutes()' list (track numbers, (track, channel) pairs).
    Answers what the Maker asks a Song (frame / tick_at / first / last / midi / ...)."""

    def __init__(self, lm, own, muted=()):
        from files.play_loaded import mute_masks, track_ports
        self.lm, self.own = lm, own
        self._starts = {}  # (piece_start per frame: asked again by bytes_in / spans / midi)
        self.ppq, self.tempo, self.map, self.edo_first = own.ppq, own.tempo, own.map, own.edo_first
        self.bends = own.bends
        n = len(lm.tracks)
        self.masks = mute_masks(muted, n)
        tport, _ = track_ports(lm)
        self.tport = tport % 16  # (Kestrel's 16 ports, as live play folds them)
        self.file_ports = set(self.tport.tolist())
        self.newch = (np.asarray(lm.rechannel, np.int64) if lm.rechannel is not None else np.full(n, -1, np.int64))
        self.playing = [j for j, t in enumerate(lm.tracks) if j not in lm.gone and self.masks[j] != 0xFFFF
                        and len(t.cp_off) and (t.notes or len(t.c_tick) or t.c_kinds is not None and len(t.c_kinds))]
        # (what decides the loaded part's sound: any change = made again, L3)
        self.key = (id(lm), lm.hide_ver, tuple(self.masks.tolist()), tuple(self.newch.tolist()),
                    tuple(self.tport.tolist()), tuple(sorted(lm.gone)))
        ends = [lm.tracks[j].end for j in self.playing if lm.tracks[j].notes]
        firsts = [lm.tracks[j].first for j in self.playing if lm.tracks[j].notes]
        self.end_tick = max([int(e) for e in ends] + ([int(own.end.max())] if len(own) else []) + [0])
        first = min([int(f) for f in firsts] + ([int(own.start[0])] if len(own) else []) + [FAR])
        self.first = self.frame(first) if first < FAR else 0
        self.last = self.frame(self.end_tick)
        self.end = np.array([self.end_tick], np.int64)  # (same_tempo / _ends_tick read song.end)
        self.longest = own.longest

    def __len__(self):
        return len(self.own) + sum(int(self.lm.tracks[j].notes) for j in self.playing)

    def frame(self, tick):
        return int(round(self.map.sec(tick) * RATE))

    def tick_at(self, frame):
        return self.own.tick_at(frame)

    # ------------------------------------------------------------ a piece

    def _same_file(self):
        """The loaded file is still the one read (size + time, as the piano roll asks: roll_loaded.still_same);
        lm.changed once it isn't (the piano roll then says it once too)."""
        from files.midi_read import MidiError
        lm = self.lm
        if getattr(lm, "changed", False):
            return False
        try:
            lm.mf.same()
        except MidiError:
            lm.changed = True
            return False
        return True

    def _read(self, f, j, lo, hi):
        """Track j's bytes lo .. hi (places in the track) as uint8: from memory (Keep in memory) or the file."""
        t = self.lm.tracks[j]
        if t.data is not None:
            return np.asarray(t.data[lo:hi], np.uint8)
        f.seek(self.lm.mf.tracks[j].start + lo)
        return np.frombuffer(f.read(hi - lo), np.uint8)

    def _held_starts(self, f, j, c, keys, ends, floor):
        """Where the notes on these keys sounding at track j's checkpoint c began: back over the checkpoints while
        each key still sounds there with the same newest end (= the same note); the stretch where that changes is
        walked for its last note-on. Older than floor = floor."""
        t = self.lm.tracks[j]
        out = np.full(len(keys), floor, np.int64)
        todo = {int(q): i for i, q in enumerate(keys.tolist())}
        want = dict(zip(keys.tolist(), ends.tolist()))
        loop, skip = _loop(), self.lm.skip_of(j)
        m = c - 1
        while todo and m >= 0 and t.cp_tick[m + 1] > floor:
            e0, e1 = int(t.cp_ent[m]), int(t.cp_ent[m + 1])
            here = dict(zip(t.e_q[e0:e1].astype(np.int64).tolist(), t.e_end[e0:e1].astype(np.int64).tolist()))
            gone = [q for q in todo if here.get(q) != want[q]]
            if gone:
                lo, hi = int(t.cp_off[m]), int(t.cp_off[m + 1])
                b = self._read(f, j, lo, hi)
                cnt, vel = np.zeros(midi_piece.QS, np.int64), np.zeros(midi_piece.QS, np.int64)
                ontk = np.full(midi_piece.QS, -1, np.int64)
                sk = skip[(skip >= lo) & (skip < hi)]
                loop(b, lo, int(t.cp_tick[m]), int(t.cp_run[m]), FAR, FAR, sk, cnt, vel, ontk,
                     int(self.masks[j]), -1, 0, np.zeros(0, np.uint8))
                for q in gone:
                    if ontk[q] >= 0:
                        out[todo[q]] = max(floor, int(ontk[q]))
                    del todo[q]
            m -= 1
        return out

    def _held_copies(self, f, j, c, b, lo, sk, cnt, held, vel, floor, t_pre, mask):
        """(PLAN B1) Each copy sounding at t_pre on track j, where it began: (ticks, places, velocities), one row a
        copy. Notes pair first in, first out, so a place's n copies there are its NEWEST n note-ons before t_pre:
        found from checkpoint c (bytes b from byte place lo), then back over the checkpoints for the ones begun
        earlier (at most as many as the checkpoint says still sound). Older than floor / not found = floor, the
        newest velocity."""
        t = self.lm.tracks[j]
        ons, _ = _held_loops()
        need = np.zeros(midi_piece.QS, np.int64)
        need[held] = cnt[held]
        parts = []

        def take(data, base, tick, run, stop, skp, want):
            off = np.zeros(midi_piece.QS + 1, np.int64)
            off[1:] = np.cumsum(want)
            tk, vl = np.zeros(int(off[-1]), np.int64), np.zeros(int(off[-1]), np.int64)
            pos = np.zeros(midi_piece.QS, np.int64)
            ons(data, base, tick, run, stop, skp, mask, want, off, tk, vl, pos)
            got = np.minimum(pos, want)
            if got.any():  # (a ring's first got places are filled, wrapped or not)
                cum = np.cumsum(got)
                idx = np.repeat(off[:-1], got) + np.arange(int(cum[-1])) - np.repeat(cum - got, got)
                parts.append((tk[idx], np.repeat(np.arange(midi_piece.QS), got), vl[idx]))
            return got

        rest = need - take(b, lo, int(t.cp_tick[c]), int(t.cp_run[c]), t_pre, sk, need)
        skip = self.lm.skip_of(j)
        m = c - 1
        while rest.any() and m >= 0 and t.cp_tick[m + 1] > floor:
            e0, e1 = int(t.cp_ent[m + 1]), int(t.cp_ent[m + 2])
            there = np.zeros(midi_piece.QS, np.int64)  # (sounding at checkpoint m + 1: begun before it at most)
            there[t.e_q[e0:e1].astype(np.int64)] = t.e_cnt[e0:e1]
            np.minimum(rest, there, out=rest)
            if not rest.any():
                break
            lo2, hi2 = int(t.cp_off[m]), int(t.cp_off[m + 1])
            rest = rest - take(self._read(f, j, lo2, hi2), lo2, int(t.cp_tick[m]), int(t.cp_run[m]), FAR,
                               skip[(skip >= lo2) & (skip < hi2)], rest)
            m -= 1
        if rest.any():
            keys = np.repeat(np.arange(midi_piece.QS), rest)
            parts.append((np.full(len(keys), floor, np.int64), keys, vel[keys]))
        if not parts:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
        tk, qs, vl = (np.concatenate(x) for x in zip(*parts))
        return np.maximum(tk, floor), qs, vl

    def _setup(self, t_pre):
        """{port: [(status, data 1, data 2)]}: what's in force on each port + channel before t_pre, from ALL the
        playing tracks together (hunt 2026-11-03: a set-up track, or one that ended, set a channel's instrument /
        volume for the others: per track it was lost after the first piece). Newest wins (same tick: the later
        track). Order kept where it matters: the bank as it was at the last instrument, then the bank now; pitch
        bend range / other RPN / NRPN as whole select + value groups, then the selection in force; "reset all
        controllers" drops what it resets. Muted channels left out, Track Properties' channel / port followed."""
        ticks, codes, tracks = [], [], []
        for n, j in enumerate(self.playing):
            t = self.lm.tracks[j]
            if not len(t.c_tick):
                continue
            k = int(np.searchsorted(t.c_tick, t_pre, "left"))
            if not k:
                continue
            c = t.c_code[:k].astype(np.int64)
            keep = ((c & 0xF0) != 0xA0) & (((int(self.masks[j]) >> (c & 15)) & 1) == 0)
            c = c[keep]
            if not len(c):
                continue
            nc = int(self.newch[j])
            if nc >= 0:
                c = (c & ~np.int64(15)) | nc
            codes.append(c | (np.int64(self.tport[j]) << 24))
            ticks.append(t.c_tick[:k][keep].astype(np.int64))
            tracks.append(np.full(len(c), n, np.int64))
        if not codes:
            return {}
        tick, code, trk = np.concatenate(ticks), np.concatenate(codes), np.concatenate(tracks)
        code = code[np.lexsort((np.arange(len(code)), trk, tick))]  # (in time; same tick: track order, then file)
        out = {}
        chan = code >> 24 << 4 | (code & 15)
        for pc in np.unique(chan).tolist():
            rows = _channel_rows(code[chan == pc])
            if rows:
                out.setdefault(pc >> 4, []).extend(rows)
        return out

    def _sysex(self, f, j):
        """Track j's SysEx messages before its first note (GM / GS / XG resets, drum part settings: set-up, hunt
        2026-11-03: lost after the first piece) as [(tick, the event's bytes after its wait)], read once a MIDI."""
        kept = getattr(self.lm, "_ahead_sysex", None)
        if kept is None:
            kept = self.lm._ahead_sysex = {}
        got = kept.get(j)
        if got is None:
            limit = int(self.lm.tracks[j].limit)
            size = 4096  # (most tracks reach their first note in a few bytes: read more only when needed)
            while True:
                got, done = _sysex_before_notes(self._read(f, j, 0, min(limit, size)))
                if done or size >= min(limit, SYSEX_SCAN):
                    break
                size *= 16
            kept[j] = got
        return got

    def bytes_in(self, f0, f1):
        """About how many of the file's bytes a piece f0 .. f1 copies: each playing track's checkpoints around it
        (the index only, nothing read; from the same early start as midi(), clamped before the last note)."""
        return self._span_bytes(self.piece_start(f0)[1], self.tick_at(f1 + int(POST_S * RATE)))

    def _span_bytes(self, t_pre, t_copy):
        """About how many bytes the piece from tick t_pre to t_copy WRITES (the events copied: what Merlin gets and
        holds). M1 2026-11-03: not what's READ (each track from its checkpoint: Shinki's 2,788 tracks read ~320 MB
        for ANY piece, freed track by track), so a short piece isn't counted as big."""
        ticks, cum = self._curve()
        lo, hi = np.interp([t_pre, t_copy], ticks, cum)
        return max(0, int(hi - lo))

    def _curve(self):
        """(ticks, bytes so far) of all playing tracks together, straight between each track's checkpoints (the
        index only; kept on the LoadedMidi per set of playing tracks: a Play with the same mutes reuses it)."""
        key = tuple(self.playing)
        kept = getattr(self.lm, "_ahead_curve", None)
        if kept is not None and kept[0] == key:
            return kept[1]
        tk, ds, jp = [], [], []  # (each span: its start tick + slope, its end tick - slope; no ticks = a jump)
        for j in self.playing:
            t = self.lm.tracks[j]
            a = np.append(t.cp_tick.astype(np.int64), max(int(t.last_tick), int(t.cp_tick[-1])))
            o = np.append(t.cp_off.astype(np.float64), float(t.limit))
            da, do = np.diff(a), np.diff(o)
            flat = da <= 0
            slope = np.where(flat, 0.0, do / np.maximum(da, 1))
            tk += [a[:-1], a[1:], a[:-1][flat]]
            ds += [slope, -slope, np.zeros(int(flat.sum()))]
            jp += [np.zeros(len(slope)), np.zeros(len(slope)), do[flat]]
        if not tk:
            got = (np.array([0, 1], np.int64), np.zeros(2))
        else:
            t_all, d_all, j_all = np.concatenate(tk), np.concatenate(ds), np.concatenate(jp)
            order = np.argsort(t_all, kind="stable")
            t_all, d_all, j_all = t_all[order], d_all[order], j_all[order]
            slope = np.cumsum(d_all)  # (the slope after each point)
            rise = np.concatenate([[0.0], slope[:-1] * np.diff(t_all)])
            got = (t_all, np.cumsum(rise) + np.cumsum(j_all))
        self.lm._ahead_curve = (key, got)
        return got

    def spans(self, f0, f1, most):
        """[(a, b)] frames f0 .. f1 cut so each part's piece copies at most `most` bytes (M1: Shinki's wall held 5 GB
        in one 2 s block; the parts are made one after another and joined), none under SPAN_LEAST."""
        if f1 - f0 <= SPAN_LEAST or self.bytes_in(f0, f1) <= most:
            return [(f0, f1)]
        mid = f0 + (f1 - f0) // 2
        return self.spans(f0, mid, most) + self.spans(mid, f1, most)

    def blocks_fit(self, first, count, most):
        """How many of the blocks from block `first` (count at most, 1 at least) one piece takes within `most`
        bytes copied (Maker: dense parts in shorter pieces)."""
        from files.ahead_pieces import BLOCK
        n = count
        while n > 1 and self.bytes_in(first * BLOCK, (first + n) * BLOCK) > most:
            n -= 1
        return n

    def piece_start(self, f0):
        """(the tick the piece starts at, the early start's tick): PRE early, LESS in dense walls (the early part
        copies at most PRE_MOST bytes: Shinki's end holds ~1.1 GB a second, M1 2026-11-03; held notes are struck
        from their start either way), your held notes from their start (Song.piece_start); the loaded MIDI's held
        ones are found in midi()."""
        key = (f0,)
        hit = self._starts.get(key)
        if hit is not None:
            return hit
        at = min(f0, self.last)
        pre = PRE
        for pre in PRE_STEPS:
            a = max(0, at - int(pre * RATE))
            if self._span_bytes(self.tick_at(a), self.tick_at(at)) <= PRE_MOST:
                break
        got = self.own.piece_start(f0, self.last, pre)
        if len(self._starts) > 256:
            self._starts.clear()
        self._starts[key] = got
        return got

    def pre_frames(self, f0):
        return f0 - self.frame(self.piece_start(f0)[0])

    def midi(self, f0, f1):
        """The piece f0 .. f1 (frames) as MIDI file bytes + the frames to drop at its start (None: silence)."""
        lm = self.lm
        t_copy = self.tick_at(f1 + int(POST_S * RATE))
        t0, t_pre = self.piece_start(f0)
        # (past the last note the piece starts PRE before it: the floor from there too, never after t_pre; a
        # floor after it gave a negative wait = _vlq's endless loop, 22 GB on Shinki's ring-out)
        floor = min(t_pre, self.tick_at(max(0, min(f0, self.last) - int(HELD_PRE * RATE))))
        loop = _loop()
        got = []
        exact_left = HELD_EXACT_MOST  # (held copies this piece may still strike at their own note-on)
        from_file = any(lm.tracks[j].data is None for j in self.playing)
        if from_file and not self._same_file():  # (L3: changed on disk = nothing more read, said: playing stops)
            raise SynthError("load.file_changed")
        with open(lm.path, "rb") if from_file else _NoFile() as f:
            for j in self.playing:
                t = lm.tracks[j]
                if t.last_tick < t_pre or (t.notes and t.first > t_copy and not len(t.c_tick)):
                    continue
                c = max(0, int(np.searchsorted(t.cp_tick, t_pre, "right")) - 1)
                k = int(np.searchsorted(t.cp_tick, t_copy, "right"))
                lo = int(t.cp_off[c])
                hi = int(t.cp_off[k]) if k < len(t.cp_off) else int(t.limit)
                b = self._read(f, j, lo, hi)
                skip = lm.skip_of(j)
                sk = skip[(skip >= lo) & (skip < hi)]
                cnt, vel = np.zeros(midi_piece.QS, np.int64), np.full(midi_piece.QS, 64, np.int64)
                ontk = np.full(midi_piece.QS, -1, np.int64)
                ends = np.full(midi_piece.QS, -1, np.int64)
                e0, e1 = int(t.cp_ent[c]), int(t.cp_ent[c + 1])
                qs = t.e_q[e0:e1].astype(np.int64)
                cnt[qs], vel[qs], ends[qs] = t.e_cnt[e0:e1], t.e_vel[e0:e1], t.e_end[e0:e1]
                lanes, counts = lm.hidden_open(j, lo)  # (hidden notes sounding there: not struck)
                if len(lanes):
                    np.subtract.at(cnt, np.asarray(lanes, np.int64), np.asarray(counts, np.int64))
                    np.maximum(cnt, 0, out=cnt)
                mask = int(self.masks[j])
                if mask:  # (muted channels' notes: not struck)
                    chans = np.arange(midi_piece.QS) // 256
                    cnt[((mask >> chans) & 1) == 1] = 0
                cnt[np.arange(midi_piece.QS) % 256 > 127] = 0
                out = np.zeros(len(b) + len(b) * 2 // 3 + 64, np.uint8)
                # (the copy's waits count from t_pre: the held notes / controllers are written before it)
                w, _, bad = loop(b, lo, int(t.cp_tick[c]), int(t.cp_run[c]), t_pre, t_copy, sk, cnt, vel, ontk,
                                 mask, int(self.newch[j]), t_pre, out)
                held = np.flatnonzero(cnt > 0)
                if not w and not len(held):
                    continue
                copies = int(cnt[held].sum())
                if copies <= exact_left:  # (each copy at its own note-on, PLAN B1)
                    exact_left -= copies
                    start, qs, vels = self._held_copies(f, j, c, b, lo, sk, cnt, held, vel, floor, t_pre, mask)
                    reps = None
                else:  # (over HELD_EXACT_MOST: one tick a key, the newest note-on)
                    start = ontk[held].copy()
                    old = start < 0  # (began before the checkpoint)
                    if old.any():
                        start[old] = self._held_starts(f, j, c, held[old], ends[held[old]], floor)
                    start, qs, vels, reps = np.maximum(start, floor), held, vel[held], cnt[held]
                # (its own small copy: the walk's buffer (1.67x the read) and the read let go at once, M1)
                body = out[:w].tobytes()
                del out, b
                got.append((j, body, start, qs, vels, reps))
            sysex = {}  # port -> [(tick, track order, bytes)]
            for n, j in enumerate(self.playing):
                for tk, ev in self._sysex(f, j):
                    if tk < t_pre:
                        sysex.setdefault(int(self.tport[j]), []).append((tk, n, ev))
        rows = self._setup(t_pre)
        origin = min([t0] + [int(g[2].min()) for g in got if len(g[2])])
        own = self.own.tracks(origin, t_pre, t_copy, self.file_ports)
        if not got and not own:
            return None, 0
        ports = sorted(set(rows) | set(sysex))
        count = 1 + len(ports) + len(got) + len(own)
        buf = bytearray(b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + min(65535, count).to_bytes(2, "big")
                        + self.ppq.to_bytes(2, "big"))
        buf += self.own.tempo_track(origin, t_copy)
        for port in ports:  # (each port's set-up first: SysEx in order, then every channel's state; hunt 11-03)
            head = bytearray(b"\x00\xff\x21\x01" + bytes([port]))
            for _, _, ev in sorted(sysex.get(port, ()), key=lambda e: e[:2]):
                head += b"\x00" + ev
            for s, d1, d2 in rows.get(port, ()):
                head += bytes([0, s, d1]) + (b"" if (s & 0xF0) in (0xC0, 0xD0) else bytes([d2]))
            head += b"\x00\xff\x2f\x00"
            buf += b"MTrk" + len(head).to_bytes(4, "big") + head
        got.reverse()  # (popped from the end: each track's body let go once it's in buf)
        while got:
            j, body, start, held, vel, cnt = got.pop()
            nc = int(self.newch[j])
            head = bytearray(b"\x00\xff\x21\x01" + bytes([int(self.tport[j])]))
            last = origin
            order = np.argsort(start, kind="stable")
            if cnt is None:  # (each copy where it began, PLAN B1)
                if len(order):
                    mem = np.zeros(13 * len(order) + 16, np.uint8)
                    w, last = _held_loops()[1](start[order], held[order], vel[order], nc, origin, mem)
                    head += mem[:w].tobytes()
                    del mem
                last = int(last)
            else:
                for i in order.tolist():  # (a key's copies together, at its newest note-on)
                    q = int(held[i])
                    ch = nc if nc >= 0 else q >> 8
                    on = bytes([0x90 | ch, q & 255, max(1, int(vel[i]))])
                    head += _vlq(int(start[i]) - last) + on + (b"\x00" + on) * (int(cnt[i]) - 1)
                    last = int(start[i])
            # (the copy's first wait was counted from t_pre: the gap from the last held note to t_pre first)
            head += _vlq(t_pre - last) + b"\xff\x01\x00"
            buf += b"MTrk" + (len(head) + len(body) + 4).to_bytes(4, "big")
            buf += head
            buf += body
            buf += b"\x00\xff\x2f\x00"
            del body
        for chunk in own:
            buf += chunk
        return buf, f0 - self.frame(origin)

    def bass_events(self):
        raise NotImplementedError("BASSMIDI pieces of a loaded MIDI: PLAN.md L4")


class _NoFile:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


SYSEX_SCAN = 1 << 20  # bytes of a track looked through for SysEx before its first note (set-up sits at the start)
_RPN = (6, 38, 98, 99, 100, 101)  # data entry MSB / LSB, NRPN LSB / MSB, RPN LSB / MSB: kept as groups
_BANK = (0, 32)
_RESET = 121  # reset all controllers: drops these (GM's list) where they came before it
_RESETS = (1, 11, 64, 65, 66, 67)


def _channel_rows(code):
    """One port + channel's events (status | data 1 << 8 | data 2 << 16, in time order) -> the rows that put it in
    the same state at a piece's start (see LoadedSong._setup)."""
    st = code & 0xF0
    d1, d2 = (code >> 8) & 127, (code >> 16) & 127
    ch = int(code[0] & 15)
    cc = st == 0xB0
    reset = np.flatnonzero(cc & (d1 == _RESET))
    last_reset = int(reset[-1]) if len(reset) else -1
    pos = np.arange(len(code))
    resets = (cc & np.isin(d1, _RESETS)) | (st == 0xE0) | (st == 0xD0)
    live = ~(resets & (pos < last_reset)) & ~(cc & (d1 == _RESET))
    rows = []
    # instrument: the bank as it was at the last program change, the program, then the bank now
    prog = np.flatnonzero(st == 0xC0)
    bank_now = {b: np.flatnonzero(cc & (d1 == b)) for b in _BANK}
    if len(prog):
        p = int(prog[-1])
        for b in _BANK:
            before = bank_now[b][bank_now[b] < p]
            if len(before):
                rows.append((0xB0 | ch, b, int(d2[before[-1]])))
        rows.append((0xC0 | ch, int(d1[p]), 0))
    for b in _BANK:
        if len(bank_now[b]) and (not len(prog) or bank_now[b][-1] > prog[-1]):
            rows.append((0xB0 | ch, b, int(d2[bank_now[b][-1]])))
    # plain controllers, bend, pressure: the last of each still in force
    plain = live & ((cc & ~np.isin(d1, _RPN + _BANK)) | (st == 0xE0) | (st == 0xD0))
    idx = np.flatnonzero(plain)
    if len(idx):
        key = np.where(cc[idx], d1[idx], 200 + (st[idx] >> 4))
        _, first = np.unique(key[::-1], return_index=True)
        for i in idx[np.sort(len(idx) - 1 - first)].tolist():
            s = int(st[i])
            rows.append((s | ch, int(d1[i]), 0 if s == 0xD0 else int(d2[i])))
    # RPN / NRPN: each parameter's last values, sent with its own selection; then the selection in force
    idx = np.flatnonzero(cc & np.isin(d1, _RPN))
    if len(idx):
        sel = [None, None, None]  # (kind "r" / "n", MSB, LSB)
        vals = {}
        for i in idx.tolist():
            n, v = int(d1[i]), int(d2[i])
            if n in (101, 99):
                sel = ["r" if n == 101 else "n", v, sel[2] if sel[0] == ("r" if n == 101 else "n") else None]
            elif n in (100, 98):
                sel = ["r" if n == 100 else "n", sel[1] if sel[0] == ("r" if n == 100 else "n") else None, v]
            elif sel[0] is not None and sel[1] is not None and sel[2] is not None and (sel[1], sel[2]) != (127, 127):
                p = vals.setdefault(tuple(sel), {})
                p.pop(n, None)
                p[n] = v
                vals[tuple(sel)] = vals.pop(tuple(sel))  # (the newest set last)
        for (kind, msb, lsb), p in vals.items():
            a, b = (101, 100) if kind == "r" else (99, 98)
            rows += [(0xB0 | ch, a, msb), (0xB0 | ch, b, lsb)]
            rows += [(0xB0 | ch, n, v) for n, v in p.items()]
        if sel[0] is not None and sel[1] is not None and sel[2] is not None:
            a, b = (101, 100) if sel[0] == "r" else (99, 98)
            rows += [(0xB0 | ch, a, sel[1]), (0xB0 | ch, b, sel[2])]
        elif vals:
            rows += [(0xB0 | ch, 101, 127), (0xB0 | ch, 100, 127)]  # (as GM advises after setting one)
    return rows


def _sysex_before_notes(b):
    """([(tick, F0 / F7 event bytes)] in a track's bytes up to its first note-on, done): done = it got there (or
    to the track's end / a broken event); False = b ended first (read more)."""
    out, p, n, tick, run = [], 0, len(b), 0, 0
    b = bytes(b)
    while p < n:
        d = 0
        while p < n:
            c = b[p]
            p += 1
            d = (d << 7) | (c & 127)
            if c < 128:
                break
        tick += d
        if p >= n:
            return out, False
        s = b[p]
        if s in (0xF0, 0xF7):
            q, ln = p + 1, 0
            while q < n:
                c = b[q]
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            if q + ln > n:
                return out, False
            out.append((tick, b[p:q + ln]))
            p = q + ln
            continue
        if s == 0xFF:
            if p + 1 >= n:
                return out, False
            if b[p + 1] == 0x2F:
                return out, True
            q, ln = p + 2, 0
            while q < n:
                c = b[q]
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            p = q + ln
            continue
        if s & 0x80:
            run = s
            p += 1
        if run < 0x80:
            return out, True  # (a broken track: stop looking)
        hi = run & 0xF0
        if hi == 0x90:
            if p + 1 >= n:
                return out, False
            if b[p + 1] > 0:
                return out, True  # (the first note: set-up is before it)
        p += 1 if hi in (0xC0, 0xD0) else 2
    return out, False


def _vlq(n):
    n = max(0, n)  # (a negative wait never ends the loop below)
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def song_for(lm, own, muted=()):
    """A LoadedSong (lm not None) or own itself."""
    return own if lm is None else LoadedSong(lm, own, muted)


__all__ = ["LoadedSong", "song_for", "POST_S", "PRE", "Song"]
