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
from files.synth import RATE


FAR = midi_piece.FAR
PRE_MOST = 64 << 20  # bytes the early start (PRE) writes at most: shorter in dense walls (Shinki 440 s: 0.5 s) ...
PRE_STEPS = (PRE, 0.5, 0.25, 0.1, 0.05, 0.02)  # ... trying these seconds (the last one goes whatever it costs)
SPAN_LEAST = RATE // 20  # (50 ms: a piece is never cut shorter, however dense: each part reads ~320 MB again on
# a 2,788-track MIDI)


def _loop():
    from files import speed
    fast = speed.loops()
    return fast.piece_track if fast is not None else midi_piece.piece_track


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

    def _cc_state(self, j, t_pre, newch):
        """Track j's controllers / instruments / bends / channel pressure in force before t_pre: the last of each,
        as (status, data 1, data 2) rows (its muted channels left out)."""
        t = self.lm.tracks[j]
        if not len(t.c_tick):
            return []
        k = int(np.searchsorted(t.c_tick, t_pre, "left"))
        codes = t.c_code[:k].astype(np.int64)
        st = codes & 0xF0
        codes = codes[(st != 0xA0) & (((int(self.masks[j]) >> (codes & 15)) & 1) == 0)]
        if not len(codes):
            return []
        st = codes & 0xF0
        key = np.where(st == 0xB0, codes & 0xFFFF, codes & 0xFF)
        _, first = np.unique(key[::-1], return_index=True)
        rows = []
        for c in codes[np.sort(len(key) - 1 - first)].tolist():
            s = c & 0xFF
            if newch >= 0:
                s = (s & 0xF0) | newch
            rows.append((s, (c >> 8) & 127, (c >> 16) & 127))
        return rows

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
        with open(lm.path, "rb") if any(lm.tracks[j].data is None for j in self.playing) else _NoFile() as f:
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
                start = ontk[held].copy()
                old = start < 0  # (began before the checkpoint)
                if old.any():
                    start[old] = self._held_starts(f, j, c, held[old], ends[held[old]], floor)
                start = np.maximum(start, floor)
                rows = self._cc_state(j, t_pre, int(self.newch[j]))
                if not w and not len(held) and not rows:
                    continue
                # (its own small copy: the walk's buffer (1.67x the read) and the read let go at once, M1)
                body = out[:w].tobytes()
                del out, b
                got.append((j, body, held, start, cnt[held], vel[held], rows))
        origin = min([t0] + [int(g[3].min()) for g in got if len(g[3])])
        own = self.own.tracks(origin, t_pre, t_copy, self.file_ports)
        if not got and not own:
            return None, 0
        count = 1 + len(got) + len(own)
        buf = bytearray(b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + min(65535, count).to_bytes(2, "big")
                        + self.ppq.to_bytes(2, "big"))
        buf += self.own.tempo_track(origin, t_copy)
        got.reverse()  # (popped from the end: each track's body let go once it's in buf)
        while got:
            j, body, held, start, cnt, vel, rows = got.pop()
            nc = int(self.newch[j])
            head = bytearray(b"\x00\xff\x21\x01" + bytes([int(self.tport[j])]))
            for s, d1, d2 in rows:
                head += bytes([0, s, d1]) + (b"" if (s & 0xF0) in (0xC0, 0xD0) else bytes([d2]))
            last = origin
            for i in np.argsort(start, kind="stable").tolist():  # (each held note struck where it began)
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
