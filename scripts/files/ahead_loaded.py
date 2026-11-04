"""Rendered ahead with a LOADED MIDI (PLAN.md L1): LoadedSong = what the pieces are made of, the loaded MIDI + your
own shapes (an ahead_pieces.Song) on the same clock (the tempo as heard). A piece of it is a small MIDI file:
- the heard tempo (a track of its own; the file's own tempo events inside the copied bytes are made text),
- each loaded track that plays (not muted / removed, with events there): its port (as live play numbers them), the
  controllers / instruments / bends in force at the piece's early start (the CC events kept in memory), the notes
  sounding there struck where they began (found back over the checkpoints, at most HELD_PRE early), then its own
  bytes COPIED as they are to the piece's end, then up to POST_S past it only the offs of notes still open
  (files/midi_piece.py piece_track),
- your shapes' notes + CC lines (Song.tracks), channel 10 melodic only on ports the loaded MIDI doesn't use.
Measured (dev/scratch/loaded_piece_bytes.py + lp_compare.py): the same sound as Merlin's render of the whole file
over that stretch (-113..-164 dB), Shinki 4.5 s of music made in ~2 s."""

import os
from collections import OrderedDict

import numpy as np

from files import midi_piece
from files.ahead_pieces import HELD_PRE, PIECE_MOST, POST_S, PRE, Song
from files.synth import RATE, SynthError


FAR = midi_piece.FAR
PRE_MOST = 64 << 20  # bytes the early start (PRE) writes at most: shorter in dense walls (Shinki 440 s: 0.5 s) ...
PRE_STEPS = (PRE, 0.5, 0.25, 0.1, 0.05, 0.02)  # ... trying these seconds (the last one goes whatever it costs)
SPAN_LEAST = RATE // 20  # (50 ms: a piece is never cut shorter, however dense: each part reads ~320 MB again on
# a 2,788-track MIDI)
HELD_EXACT_MOST = 1 << 24  # held copies a piece strikes EACH AT ITS OWN NOTE-ON (PLAN B1, 2026-11-04: all of a key's
# copies struck at one tick = a burst of that key at block edges, +15..+27 dB, Shinki 442 s); over it (Shinki's end
# holds 788 M copies = 12 GB of starts) the rest go the old way: one tick a key
MERGED_READ_MOST = 512 << 20  # bytes read again to find the copies sounding as all tracks pair them (PLAN B3); more
# = each track's own copies (B1)
MERGED_STEPS_S = (0.25, 2.0)  # ... over these stretches before the early start, the slots still short only in the
# next (older copies: each track's own); busy music finds its newest copies in the first quarter second
CARRY_KEPT_ROWS = 32 << 20  # rows of hand-overs kept in all (PLAN E: a jump back or a remake picks the chain up from
CARRY_KEPT_MOST = 4096  # ... the latest one before it; 19 bytes a row: ~600 MB at most) and at most this many of them
LEAD_STEP_S = 0.5  # (a lead-up looks back this far first, doubling; then halves down to within this)
LEAD_MOST = 4 << 30  # bytes a lead-up's dry part copies at most, less with little free memory (as a counted piece,
# ahead_pieces.COUNTED_MOST: Shinki's wall in 82 ms parts took 6 x 8 s, each reading the tracks' checkpoints again)


def _loop():
    from files import speed
    fast = speed.loops()
    return fast.piece_track if fast is not None else midi_piece.piece_track


def _note_ons_loop():
    from files import speed
    fast = speed.loops()
    return fast.piece_note_ons if fast is not None else midi_piece.note_ons


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

    counts_notes = True  # (midi(counted=True) for our Merlin: stacks of identical notes sent once, PLAN D2)

    def counted_here(self):
        """Merlin pieces of this song go counted (PLAN D2): our connector reads repeat marks, and no shapes of your
        own ride along (their tracks can't be ordered against counted notes). The Maker sizes them bigger."""
        from files import kestrel
        return not len(self.own) and kestrel.repeat_marks()

    def __init__(self, lm, own, muted=()):
        from files.play_loaded import mute_masks, track_ports
        self.lm, self.own = lm, own
        self._starts = {}  # (piece_start per frame: asked again by bytes_in / spans / midi)
        self._led = set()  # (ticks a lead-up was tried for, PLAN E: once each)
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
                     int(self.masks[j]), -1, 0, np.zeros(0, np.uint8), FAR, np.zeros(midi_piece.QS, np.int64), 0, 0,
                     *_NO_EVENTS)
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
        if b is None:  # (read again: checkpoint c's stretch)
            lo = int(t.cp_off[c])
            hi = int(t.cp_off[c + 1]) if c + 1 < len(t.cp_off) else int(t.limit)
            b = self._read(f, j, lo, hi)
            skip = self.lm.skip_of(j)
            sk = skip[(skip >= lo) & (skip < hi)]
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

    _NONE = (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64))

    def _walk_sliced(self, f, j, c, k, t_pre, t_copy, cnt, vel, ontk, t_on, opn, floor, runs, loop):
        """(PLAN D2, counted pieces) piece_track over track j's checkpoints c .. k, about COUNTED_SLICE bytes at a
        time (a 2 GB track's stretch: its read + room for all its notes was GBs, 10.7 GB at Shinki's 457 s). Each
        slice starts at a checkpoint (its tick + running status); carried on: the counts before t_pre (cnt) until
        the copy starts, then the notes open (opn), and the last tick written. Returns the track's body (bytes)."""
        t = self.lm.tracks[j]
        skip = self.lm.skip_of(j)
        mask, nc, port = int(self.masks[j]), int(self.newch[j]), int(self.tport[j])
        offs, n_cp = t.cp_off, len(t.cp_off)
        parts, last, a = [], t_pre, c
        opn[:] = -1  # (the copy not started yet: piece_track sets opn from cnt when it starts)
        while a < k:
            e = min(max(int(np.searchsorted(offs, int(offs[a]) + COUNTED_SLICE, "right")) - 1, a + 1), k)
            lo = int(offs[a])
            hi = int(offs[e]) if e < n_cp else int(t.limit)
            b = self._read(f, j, lo, hi)
            sk = skip[(skip >= lo) & (skip < hi)]
            out = np.zeros(len(b) + len(b) * 2 // 3 + 64, np.uint8)
            started = opn[0] >= 0
            evs = runs.room(len(b) // 3 + 1)
            # (to the stretch's end, as one walk reads: piece_track says the same when it ran past t_copy and when
            # its bytes simply ended)
            w, last, _ = loop(b, lo, int(t.cp_tick[a]), int(t.cp_run[a]), t_pre, t_copy, sk,
                              opn.copy() if started else cnt, vel, ontk, mask, nc, int(last), out, t_on, opn, port,
                              floor, *evs)
            runs.took()
            parts.append(out[:w].tobytes())
            del out, b
            a = e
        return b"".join(parts)

    def _gslots(self, j):
        """Track j's places (channel x 256 + key) as the synth pairs them: port << 12 | channel played << 8 | key."""
        q = np.arange(midi_piece.QS, dtype=np.int64)
        nc = int(self.newch[j])
        ch = np.full(len(q), nc, np.int64) if nc >= 0 else q >> 8
        return (np.int64(self.tport[j]) << 12) | (ch << 8) | (q & 255)

    def _merged_copies(self, f, pend, floor, t_pre):
        """(PLAN B3) {track: (ticks, places, velocities)}: the copies sounding at t_pre as a whole render pairs them.
        The synth pairs ALL tracks together (an off ends its port + channel + key's OLDEST copy, whichever track:
        Kestrel / Merlin / BASSMIDI alike), so the copies sounding = each slot's NEWEST G note-ons over all tracks,
        G = the tracks' counts added up (Shinki 313 s: per track a key held 11,316 copies a whole render had ended).
        No rounds over everything: all tracks' note-ons of the last MERGED_STEPS_S[0] before t_pre (read again; busy
        music holds its newest copies there) give each slot's newest G exactly; slots with fewer there look in the
        next stretch (only they); still short = the rest from the tracks' own copies (B1, pend's 7th) begun
        before, newest first; then at the floor. None = more than MERGED_READ_MOST to read again (the caller uses the own copies)."""
        lm = self.lm
        G = np.zeros(1 << 16, np.int64)
        gmap = {}
        for j, p in pend.items():
            gmap[j] = self._gslots(j)
            np.add.at(G, gmap[j][p[1]], p[2])
        ons = _note_ons_loop()
        frame_pre = self.frame(t_pre)
        read = 0

        def stretch(w_from, w_to, limit):
            """Every track's note-ons from w_from to before w_to on slots with limit > 0, each track's newest
            limit[slot] a slot (more never needed) -> rows (ticks, gslots, places, velocities, tracks) or None
            (over MERGED_READ_MOST)."""
            nonlocal read
            want_g = limit > 0
            rows = []
            for j in self.playing:
                t = lm.tracks[j]
                if not t.notes or t.first >= w_to or t.last_tick < w_from:
                    continue
                m0 = max(0, int(np.searchsorted(t.cp_tick, w_from, "right")) - 1)
                c = max(0, int(np.searchsorted(t.cp_tick, w_to, "right")) - 1)
                lo = int(t.cp_off[m0])
                hi = int(t.cp_off[c + 1]) if c + 1 < len(t.cp_off) else int(t.limit)
                read += hi - lo
                if read > MERGED_READ_MOST:
                    return None
                if hi <= lo:
                    continue
                g = gmap.setdefault(j, self._gslots(j))
                want = want_g[g].astype(np.int8)
                if not want.any():
                    continue
                b = self._read(f, j, lo, hi)
                skip = lm.skip_of(j)
                n = len(b) // 3 + 1
                tk, qq, vv = np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n, np.int64)
                k = ons(b, lo, int(t.cp_tick[m0]), int(t.cp_run[m0]), w_to, skip[(skip >= lo) & (skip < hi)],
                        int(self.masks[j]), want, tk, qq, vv)
                del b
                m = tk[:k] >= w_from
                tk, qq, vv = tk[:k][m], qq[:k][m], vv[:k][m]
                if not len(tk):
                    continue
                gs = g[qq]
                o = np.lexsort((-tk, gs))
                tk, gs, qq, vv = tk[o], gs[o], qq[o], vv[o]
                keep = np.arange(len(gs)) - np.searchsorted(gs, gs, "left") < limit[gs]
                rows.append((tk[keep], gs[keep].astype(np.int32), qq[keep].astype(np.int16), vv[keep].astype(np.int16),
                             np.full(int(keep.sum()), j, np.int32)))
            return rows

        out = {}
        parts = {}  # track -> [(ticks, places, velocities)]

        def newest(rows, limit):
            """rows (ticks, gslots, places, velocities, tracks): each slot's newest limit[slot] -> parts; returns
            how many each slot took."""
            if not rows:
                return np.zeros(1 << 16, np.int64)
            tk, gs, qq, vv, tr = (np.concatenate(x) for x in zip(*rows))
            o = np.lexsort((-tk, gs))
            tk, gs, qq, vv, tr = tk[o], gs[o], qq[o], vv[o], tr[o]
            keep = np.arange(len(gs)) - np.searchsorted(gs, gs, "left") < limit[gs]
            tk, gs, qq, vv, tr = tk[keep], gs[keep], qq[keep], vv[keep], tr[keep]
            o = np.argsort(tr, kind="stable")  # (by track, one sort: not each track against all)
            tr_s = tr[o]
            cut = np.flatnonzero(np.diff(tr_s)) + 1
            for a, b in zip(np.concatenate([[0], cut]).tolist(), np.concatenate([cut, [len(o)]]).tolist()):
                i = o[a:b]
                parts.setdefault(int(tr_s[a]), []).append((tk[i], qq[i].astype(np.int64), vv[i].astype(np.int64)))
            return np.bincount(gs, minlength=1 << 16)

        rest = G.copy()
        w_to = t_pre
        for back in MERGED_STEPS_S:  # (the last moment first: busy music finds its newest copies there)
            w0 = max(floor, self.tick_at(max(0, frame_pre - int(back * RATE))))
            if w0 >= w_to:
                continue
            rows = stretch(w0, w_to, rest)
            if rows is None:
                return None
            rest -= newest(rows, rest)
            w_to = w0
            if not rest.any():
                break
        if rest.any():  # (begun before the stretch: the tracks' own copies, newest first)
            old = []
            for j, p in pend.items():
                tk, qq, vv = p[6]
                m = tk < w_to
                if m.any():
                    old.append((tk[m], gmap[j][qq[m]], qq[m], vv[m], np.full(int(m.sum()), j, np.int64)))
            rest -= newest(old, rest)
        if rest.any():  # (still short: at the floor, on tracks holding that slot)
            for j, p in pend.items():
                held, cn, vl = p[1], p[2], p[3]
                take = np.minimum(cn, rest[gmap[j][held]])
                if take.any():
                    rest[gmap[j][held]] -= take
                    parts.setdefault(j, []).append((np.full(int(take.sum()), floor, np.int64), np.repeat(held, take),
                                                    np.repeat(vl, take)))
        for j, ps in parts.items():
            out[j] = tuple(np.concatenate(x) for x in zip(*ps))
        return out

    def _setup(self, t_pre):
        """{port: [(status, data 1, data 2)]}: what's in force on each port + channel before t_pre, from ALL the
        playing tracks together (hunt 2026-11-03: a set-up track, or one that ended, set a channel's instrument /
        volume for the others: per track it was lost after the first piece). Newest wins (same tick: the later
        track). Order kept where it matters: the bank as it was at the last instrument, then the bank now; pitch
        bend range / other RPN / NRPN as whole select + value groups, then the selection in force; "reset all
        controllers" drops what it resets. Muted channels left out, Track Properties' channel / port followed."""
        _, code = self._controls(-1, t_pre)
        if not len(code):
            return {}
        out = {}
        chan = code >> 24 << 4 | (code & 15)
        for pc in np.unique(chan).tolist():
            rows = _channel_rows(code[chan == pc])
            if rows:
                out.setdefault(pc >> 4, []).extend(rows)
        return out

    def _controls(self, t_from, t_to):
        """(ticks, codes) of ALL the playing tracks' controller / instrument / bend / pressure events from t_from to
        before t_to, in time (same tick: track order, then file); code = status | data 1 << 8 | data 2 << 16 | port
        << 24. Muted channels left out, Track Properties' channel / port followed, key pressure left out."""
        ticks, codes, tracks = [], [], []
        for n, j in enumerate(self.playing):
            t = self.lm.tracks[j]
            if not len(t.c_tick):
                continue
            a = int(np.searchsorted(t.c_tick, t_from, "left")) if t_from > 0 else 0
            k = int(np.searchsorted(t.c_tick, t_to, "left"))
            if k <= a:
                continue
            c = t.c_code[a:k].astype(np.int64)
            keep = ((c & 0xF0) != 0xA0) & (((int(self.masks[j]) >> (c & 15)) & 1) == 0)
            c = c[keep]
            if not len(c):
                continue
            nc = int(self.newch[j])
            if nc >= 0:
                c = (c & ~np.int64(15)) | nc
            codes.append(c | (np.int64(self.tport[j]) << 24))
            ticks.append(t.c_tick[a:k][keep].astype(np.int64))
            tracks.append(np.full(len(c), n, np.int64))
        if not codes:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        tick, code, trk = np.concatenate(ticks), np.concatenate(codes), np.concatenate(tracks)
        order = np.lexsort((np.arange(len(code)), trk, tick))
        return tick[order], code[order]

    def _replay(self, t_from, t_to):
        """{port: [(tick, status, data 1, data 2)]}: the controller changes from t_from to before t_to, in time (PLAN
        B3, 2026-11-04: a piece's held copies begun before its early start get the volume / instrument / bend of
        THEIR note-on, as a whole render gives them; set to t_to's state at once, a fade-out held the copies begun
        loud at the faded volume = silent seconds early)."""
        tick, code = self._controls(t_from, t_to)
        out = {}
        for tk, c in zip(tick.tolist(), code.tolist()):
            out.setdefault(c >> 24, []).append((tk, c & 0xFF, (c >> 8) & 127, (c >> 16) & 127))
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
        (the index only, nothing read; from the same early start as midi(), clamped before the last note). Up to
        f1 only: past it a piece copies just the offs of notes still open (PLAN B2: a few bytes)."""
        return self._span_bytes(self.piece_start(f0)[1], self.tick_at(f1))

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

    def _carries(self):
        """The carried notes kept for this sound (PLAN E): {tick a piece starts early at: what's handed to it}, oldest
        first. On the LoadedMidi (a remake or a new Play makes a new LoadedSong: the chain goes on), dropped when the
        mutes / hidden notes / tempo change."""
        from files.ahead_pieces import same_tempo
        kept = getattr(self.lm, "_ahead_carries", None)
        if kept is None or kept[0] != self.key or not same_tempo(kept[1], self):
            kept = (self.key, self, OrderedDict())
            self.lm._ahead_carries = kept
        return kept[2]

    def _keep_carry(self, carry):
        """A piece's hand-over kept (_Runs._carry); the oldest let go past CARRY_KEPT_ROWS rows in all."""
        got = self._carries()
        got.pop(carry["at"], None)
        got[carry["at"]] = carry
        rows = sum(len(c["tick"]) for c in got.values())
        while len(got) > 1 and (rows > CARRY_KEPT_ROWS or len(got) > CARRY_KEPT_MOST):
            rows -= len(got.popitem(last=False)[1]["tick"])

    def _held_guess(self, tick):
        """About how many note copies sound at tick (each playing track's checkpoint before it: the index only)."""
        n = 0
        for j in self.playing:
            t = self.lm.tracks[j]
            if not t.notes or not len(t.cp_tick):
                continue
            c = max(0, int(np.searchsorted(t.cp_tick, tick, "left")) - 1)
            n += int(t.e_cnt[int(t.cp_ent[c]):int(t.cp_ent[c + 1])].sum())
        return n

    def _lead_up(self, f0, t_pre, cancel=None):
        """(PLAN E) Nothing handed over at t_pre and more held there than HELD_EXACT_MOST (the first piece after a
        Play / jump / remake in a dense wall's ring: the newest-note-on guess struck them all at once, "128 keys
        pressed" instead of the decay; user 11-04: "you really need to wait it out"): the stretch before is walked
        counted without making sound (dry pieces, each handing its notes to the next), from the latest kept
        hand-over or a spot holding few enough, so this piece gets them as a chain of pieces would. False = not
        done (cancelled, nothing to start from, or a part couldn't go counted: the guess then)."""
        kept = self._carries()
        start = max([c["f0"] for at, c in kept.items() if at < t_pre and c["f0"] < f0] + [-1])
        ok, far, back = None, f0, LEAD_STEP_S
        least = max(self.first, int(PRE * RATE) + 1)  # (a piece starting early at the song's tick 0 isn't counted)
        while far > least:  # (back from f0, doubling, to a spot holding few enough; then halved down to it)
            a = max(least, f0 - int(back * RATE))
            if a <= start:
                break
            if self._held_guess(self.piece_start(a)[1]) <= HELD_EXACT_MOST:
                ok = a
                break
            far, back = a, back * 2
        if ok is not None:
            while far - ok > int(LEAD_STEP_S * RATE):
                m = (ok + far) // 2
                if self._held_guess(self.piece_start(m)[1]) <= HELD_EXACT_MOST:
                    ok = m
                else:
                    far = m
        start = max(start, -1 if ok is None else ok)
        if start < 0 or start >= f0:
            return False
        from files.system import free_memory
        free = free_memory()
        most = LEAD_MOST if free is None else max(LEAD_MOST >> 4, min(LEAD_MOST, free // 2))  # (as Maker.piece_most)
        parts = self.spans(start, f0, most)
        k = 0
        while k < len(parts):  # (a part hands over where the next starts early: after its own early start, else
            a, b = parts[k]    # joined with the next; a wall's parts start 0.02 s early, the next piece 1 s)
            if len(parts) > 1 and self.piece_start(b)[1] <= self.piece_start(a)[1]:
                j = k if k + 1 < len(parts) else k - 1
                parts[j:j + 2] = [(parts[j][0], parts[j + 1][1])]
                k = max(0, j - 1)
                continue
            k += 1
        for a, b in parts:
            if cancel is not None and cancel.is_set():
                return False
            try:
                self.midi(a, b, counted=True, dry=True)
            except TooBigPlain:
                return False
            if self.piece_start(b)[1] not in kept:  # (the chain broke there: no hand-over)
                return False
        return True

    def midi(self, f0, f1, counted=False, dry=False, cancel=None):
        """The piece f0 .. f1 (frames) as MIDI file bytes + the frames to drop at its start (None: silence).
        counted = for our Merlin (kestrel.repeat_marks()): the notes of all tracks sent as stacks with a count (PLAN
        D2: a dense wall's piece held 253 M note-ons, 402 k of them different), the rest as before. dry = counted,
        only its hand-over to the next piece kept, nothing written (_lead_up: (None, 0)); cancel = a threading.Event
        that stops a lead-up."""
        lm = self.lm
        t_copy = self.tick_at(f1 + int(POST_S * RATE))
        t0, t_pre = self.piece_start(f0)
        # (past the last note the piece starts PRE before it: the floor from there too, never after t_pre; a
        # floor after it gave a negative wait = _vlq's endless loop, 22 GB on Shinki's ring-out)
        floor = min(t_pre, self.tick_at(max(0, min(f0, self.last) - int(HELD_PRE * RATE))))
        t_on = self.tick_at(f1)  # (past it only the offs of notes still open, PLAN B2)
        opn = np.zeros(midi_piece.QS, np.int64)
        loop = _loop()
        # (your own shapes ride in tracks of their own, unordered against the counted notes: those pieces plain)
        runs = None
        can = counted and not len(self.own) and floor < t_pre
        lead = can and not dry and t_pre not in self._carries() and t_pre not in self._led
        if lead and self._held_guess(t_pre) > HELD_EXACT_MOST:
            self._led.add(t_pre)
            self.lead_way = self._lead_up(f0, t_pre, cancel)  # (for checks)
            if cancel is not None and cancel.is_set():
                self._led.discard(t_pre)  # (tried again when the piece is wanted again)
                return None, 0
        if can:
            runs = _Runs(self, floor, t_pre, t_copy, reorder=counted != "keep order")
            runs.dry = dry
        self.counted_way = "plain" if runs is None else counted  # (which way the last piece went: for checks)
        evs = runs.views if runs is not None else _NO_EVENTS
        carry, use = None, None  # (the notes still sounding at t_pre, handed over by the piece before: _Runs._carry)
        if runs is not None:
            got_c = self._carries().get(t_pre)
            if got_c is not None:
                use = got_c["pcs"] & ~runs.pedal
                keep = use[got_c["pk"] >> 7]
                carry = {k: (v[keep] if isinstance(v, np.ndarray) and k != "pcs" else v) for k, v in got_c.items()}
            t_next = self.piece_start(f1)[1]  # (where the piece after this one starts early)
            if t_pre < t_next <= t_on:
                runs.carry_at, runs.carry_f = t_next, f1
        self.carried_way = carry is not None  # (for checks)
        got = []
        pend = {}  # track -> (checkpoint, its held places, counts, velocities, newest on ticks, ends, own copies)
        copies = 0
        from_file = any(lm.tracks[j].data is None for j in self.playing)
        if from_file and not self._same_file():  # (L3: changed on disk = nothing more read, said: playing stops)
            raise SynthError("load.file_changed")
        with open(lm.path, "rb") if from_file else _NoFile() as f:
            for j in self.playing:
                t = lm.tracks[j]
                if t.last_tick < t_pre or (t.notes and t.first > t_copy and not len(t.c_tick)):
                    continue
                # (the checkpoint BEFORE t_pre: one on t_pre can sit amid that tick's events, the notes struck there
                # before it then held copies on t_pre = order against the counted notes lost, Shinki 458 s)
                c = max(0, int(np.searchsorted(t.cp_tick, t_pre, "left")) - 1)
                k = int(np.searchsorted(t.cp_tick, t_copy, "right"))
                lo = int(t.cp_off[c])
                hi = int(t.cp_off[k]) if k < len(t.cp_off) else int(t.limit)
                skip = lm.skip_of(j)
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
                if runs is not None and hi - lo > COUNTED_SLICE:  # (a huge track's stretch: a slice at a time)
                    body = self._walk_sliced(f, j, c, k, t_pre, t_copy, cnt, vel, ontk, t_on, opn, floor, runs, loop)
                    w, b, out = len(body), None, None
                else:
                    b = self._read(f, j, lo, hi)
                    sk = skip[(skip >= lo) & (skip < hi)]
                    out = np.zeros(len(b) + len(b) * 2 // 3 + 64, np.uint8)
                    if runs is not None:
                        evs = runs.room(len(b) // 3 + 1)
                    # (the copy's waits count from t_pre: the held notes / controllers are written before it)
                    w, _, bad = loop(b, lo, int(t.cp_tick[c]), int(t.cp_run[c]), t_pre, t_copy, sk, cnt, vel, ontk,
                                     mask, int(self.newch[j]), t_pre, out, t_on, opn, int(self.tport[j]), floor, *evs)
                    if runs is not None:
                        runs.took()
                    body = None
                held = np.flatnonzero(cnt > 0)
                if use is not None and len(held):  # (handed over by the piece before: not found track by track)
                    nc = int(self.newch[j])
                    pcs = int(self.tport[j]) * 16 + (np.full(len(held), nc, np.int64) if nc >= 0 else held >> 8)
                    held = held[~use[pcs]]
                if not w and not len(held):
                    continue
                copies += int(cnt[held].sum())
                own = None  # (each copy at its own note-on, PLAN B1: while the piece stays under HELD_EXACT_MOST)
                if copies <= HELD_EXACT_MOST:
                    own = self._held_copies(f, j, c, b, lo, None if b is None else sk, cnt, held, vel, floor, t_pre,
                                            mask)
                if body is None:  # (its own small copy: the walk's buffer (1.67x the read) and the read let go, M1)
                    body = out[:w].tobytes()
                del out, b
                got.append([j, body, None, None, None, None])
                pend[j] = (c, held, cnt[held], vel[held], ontk[held], ends[held], own)
            if lead and copies > HELD_EXACT_MOST and t_pre not in self._led:  # (the index's guess said fewer: a
                # track's checkpoints far apart; walked, the true count is over: led up now, the piece made again)
                self._led.add(t_pre)
                del got, pend
                self.lead_way = self._lead_up(f0, t_pre, cancel)
                if cancel is not None and cancel.is_set():
                    self._led.discard(t_pre)
                    return None, 0
                return self.midi(f0, f1, counted, cancel=cancel)
            merged = None
            if 0 < copies <= HELD_EXACT_MOST:  # (as a whole render pairs them: all tracks together, PLAN B3)
                merged = self._merged_copies(f, pend, floor, t_pre)
            if merged is not None:
                for e in got:
                    e[2:] = list(merged.pop(e[0], self._NONE)) + [None]
                for j in sorted(merged, key=self.playing.index):  # (tracks sounding there with nothing else)
                    got.append([j, b"", *merged[j], None])
                got.sort(key=lambda e: self.playing.index(e[0]))
            for e in got:
                if e[2] is not None:
                    continue
                j = e[0]
                c, held, cn, vl, ot, en, own = pend[j]
                if copies <= HELD_EXACT_MOST:  # (each track's own copies, PLAN B1)
                    e[2:] = [*own, None]
                else:  # (over HELD_EXACT_MOST: one tick a key, the newest note-on)
                    start = ot.copy()
                    old = start < 0  # (began before the checkpoint)
                    if old.any():
                        start[old] = self._held_starts(f, j, c, held[old], en[old], floor)
                    e[2:] = [np.maximum(start, floor), held, vl, cn]
            sysex = {}  # port -> [(tick, track order, bytes)]
            for n, j in enumerate(self.playing):
                for tk, ev in self._sysex(f, j):
                    if tk < t_pre:
                        sysex.setdefault(int(self.tport[j]), []).append((tk, n, ev))
        origin = min([t0] + [int(g[2].min()) for g in got if len(g[2])])
        if carry is not None and len(carry["tick"]):
            origin = min(origin, max(floor, int(carry["tick"].min())))
        counted_tracks = []
        if runs is not None:
            # (a SysEx copied, or a held copy on t_pre itself whose order there matters (a controller on its
            # channel at t_pre, or a note of its key at t_pre: written as a track holds it only): this piece plain)
            at_pre = [e[0] for e in got if len(e[2]) and runs.clash_at(e[0], e[2], e[3], e[4], t_pre)]
            if runs.sysex or at_pre:
                self.counted_why = ("sysex" if runs.sysex else "held at t_pre", at_pre[:5])  # (for checks)
                if dry:  # (no hand-over from here: the lead-up stops)
                    return None, 0
                if self.bytes_in(f0, f1) > PIECE_MOST:  # (sized for counted notes: plain it would take GBs)
                    raise TooBigPlain()
                return self.midi(f0, f1)
            if carry is not None:
                runs.carried(carry, floor)
            for e in got:  # (held copies too, in track order: the order a synth reads them in)
                if len(e[2]):
                    runs.held(e[0], e[2], e[3], e[4], e[5])
                    e[2:] = [_EMPTY, _EMPTY, _EMPTY, None]
            got = [e for e in got if e[1]]  # (a track left with nothing but its notes: none of its own)
            counted_tracks = runs.tracks(origin)
            if counted_tracks is None:  # (offs before ons would pair differently somewhere: tracks' order kept)
                return self.midi(f0, f1, counted="keep order", dry=dry)
            if dry:
                return None, 0
        rows = self._setup(origin)  # (the state where the piece begins, then every change up to t_pre: PLAN B3)
        replay = self._replay(origin, t_pre)
        own = self.own.tracks(origin, t_pre, t_copy, self.file_ports)
        if not got and not own and not counted_tracks:
            return None, 0
        ports = sorted(set(rows) | set(sysex) | set(replay))
        count = 1 + len(ports) + len(got) + len(own) + len(counted_tracks)
        buf = bytearray(b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + min(65535, count).to_bytes(2, "big")
                        + self.ppq.to_bytes(2, "big"))
        buf += self.own.tempo_track(origin, t_copy)
        for port in ports:  # (each port's set-up first: SysEx in order, then every channel's state; hunt 11-03)
            head = bytearray(b"\x00\xff\x21\x01" + bytes([port]))
            for _, _, ev in sorted(sysex.get(port, ()), key=lambda e: e[:2]):
                head += b"\x00" + ev
            for s, d1, d2 in rows.get(port, ()):
                head += bytes([0, s, d1]) + (b"" if (s & 0xF0) in (0xC0, 0xD0) else bytes([d2]))
            last = origin
            for tk, s, d1, d2 in replay.get(port, ()):
                head += _vlq(tk - last) + bytes([s, d1]) + (b"" if (s & 0xF0) in (0xC0, 0xD0) else bytes([d2]))
                last = tk
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
        for chunk in counted_tracks:
            buf += chunk
        for chunk in own:
            buf += chunk
        return buf, f0 - self.frame(origin)

    def bass_events(self):
        raise NotImplementedError("BASSMIDI pieces of a loaded MIDI: PLAN.md L4")


class TooBigPlain(Exception):
    """A piece sized for counted notes (Maker.piece_most, COUNTED_MOST) that has to go plain: the caller cuts it to
    plain sizes (ahead_pieces.card_piece)."""


class _NoFile:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


# (piece_track's counted-notes arguments when a piece isn't counted: bars, bar_ch, ev_key, ev_sig, nev)
_NO_EVENTS = (np.zeros(0, np.int64), np.zeros(256, np.uint8), np.zeros(0, np.int64), np.zeros(0, np.uint8),
              np.zeros(2, np.int64))
_EMPTY = np.zeros(0, np.int64)
COUNTED_BATCH = 4 << 20  # notes gathered before they're sorted + folded (PLAN D2: ~70 MB; the folded runs stay)
SORT_WORKERS = max(1, min(8, (os.cpu_count() or 2) - 2))  # threads sorting / merging counted batches (PLAN E3: the
# compiled loops let go of Python's lock; a piece is made one at a time, on the graphics card, so they're free)
_POOL = []


def _sort_pool():
    if not _POOL:
        from concurrent.futures import ThreadPoolExecutor
        _POOL.append(ThreadPoolExecutor(SORT_WORKERS, thread_name_prefix="counted sort"))
    return _POOL[0]


COUNTED_SLICE = 8 << 20  # bytes of a track walked at a time in a counted piece (checkpoints are <= 0.5 MB apart)
CARRY_ENDED_S = (5.0, 2.0, 1.0, 0.5, 0.25, 0.0)  # seconds of notes ended before a piece handed to it too (their release
# tails: Shinki 470 s needed 5 s, 1 s left a click; a dense wall starts its pieces 0.02 s early), less when ...
CARRY_ROWS_MOST = 16 << 20  # ... that is more than this many rows (19 bytes each: ~300 MB at most)


_PAIRING_CCS = np.array([64, 66, 120, 123, 124, 125, 126, 127])  # (sustain, sostenuto, all sound / notes off, modes)


def _counted_loops():
    from files import speed
    fast = speed.loops()
    if fast is not None:
        return fast.piece_runs_sort, fast.piece_runs_merge, fast.piece_counted_track, fast.piece_order_ok
    return midi_piece.runs_sort_fold, midi_piece.runs_merge, midi_piece.counted_track, midi_piece.order_ok


def _open_loop():
    from files import speed
    fast = speed.loops()
    return fast.piece_open_at if fast is not None else midi_piece.open_at


class _Runs:
    """(PLAN D2) A Merlin piece's notes from all its tracks as stacks with a count: gathered a batch at a time
    (piece_track puts them in), each batch sorted by tick + place (stable: the tracks' order kept, as a synth merges
    them) and folded (identical ones back to back = one with a count), merged into what came before. Notes on a port +
    channel with a controller at that tick inside the tracks' own bytes stay there (bars: the order between tracks
    matters there). Memory: a batch + the folded runs (a dense wall's piece: 402 k of 253 M)."""

    def __init__(self, song, base_t, t_pre, t_copy, reorder=True):
        self.song, self.base = song, base_t
        tick, code = song._controls(t_pre, t_copy + 1)
        pc = ((code >> 24) & 15) * 16 + (code & 15)
        self.bars = np.unique((tick << 8) | pc).astype(np.int64)
        self.bar_ch = np.zeros(256, np.uint8)
        self.bar_ch[np.unique(pc)] = 1
        # (bit 2: a tick's offs NOT sorted before its ons: everywhere when asked, else on channels that ever had a
        # pedal pressed / all-notes-off / mode change: they pair offs in ways counting can't follow,
        # midi_piece.order_ok. A pedal only LET GO (value < 64: Merlin's down is >= 64, and up then changes nothing)
        # doesn't count: Shinki lets go of it on 11 channels at 1.5 s, which left them out of the carried notes and
        # rang a tail on after the end)
        _, every = song._controls(-1, t_copy + 1)
        d1 = (every >> 8) & 127
        pressed = ~np.isin(d1, (64, 66)) | (((every >> 16) & 127) >= 64)
        odd = ((every & 0xF0) == 0xB0) & np.isin(d1, _PAIRING_CCS) & pressed
        self.pedal = np.zeros(256, bool)  # (port + channels pairing in ways counting can't follow: never carried)
        self.pedal[np.unique(((every[odd] >> 24) & 15) * 16 + (every[odd] & 15))] = True
        self.bar_ch[self.pedal] |= 2
        if not reorder:
            self.bar_ch |= 2
        self.carry_at = None  # (a tick the notes still sounding at are handed to the next piece: midi())
        self.carry_f = None  # (... the frame that next piece starts at)
        self.dry = False  # (a lead-up's piece: only the hand-over, no tracks written)
        self.bits = int(t_copy - base_t + 1).bit_length() + 16
        self.cap, self.n = COUNTED_BATCH, 0
        self.k = np.zeros(self.cap, np.int64)
        self.s = np.zeros(self.cap, np.uint8)
        self.c = np.ones(self.cap, np.int64)
        self.nev = np.zeros(2, np.int64)
        self.stack = []  # (folded lists, oldest first: flush)
        self.sysex = False

    @property
    def views(self):
        return self.bars, self.bar_ch, self.k[self.n:], self.s[self.n:], self.nev

    def room(self, need):
        """Room for `need` more (a track's read // 3: an event takes 3 bytes at least); piece_track's arguments."""
        if self.n + need > self.cap:
            self.flush()
            if need > self.cap:
                self.cap = need
                self.k = np.zeros(need, np.int64)
                self.s = np.zeros(need, np.uint8)
                self.c = np.ones(need, np.int64)
        self.nev[:] = 0
        return self.views

    def took(self):
        """After piece_track: what it put in counted (one each)."""
        got = int(self.nev[0])
        self.c[self.n:self.n + got] = 1
        self.n += got
        self.sysex |= bool(self.nev[1])

    def held(self, j, start, qs, vel, cnt):
        """Track j's held copies (start ticks, channel x 256 + key, velocities, counts or None = one each), in the
        order its head would write them."""
        song = self.song
        order = np.argsort(start, kind="stable")
        start, qs, vel = start[order], qs[order], vel[order]
        nc = int(song.newch[j])
        ch = np.full(len(qs), nc, np.int64) if nc >= 0 else qs >> 8
        pc = int(song.tport[j]) * 16 + ch
        on = (self.bar_ch[pc] & 2) == 0
        keys = ((start.astype(np.int64) - self.base) << 16) | (pc << 8) | ((qs & 255) << 1) | on
        n = len(keys)
        self.room(n)
        self.k[self.n:self.n + n] = keys
        self.s[self.n:self.n + n] = np.maximum(vel, 1)
        self.c[self.n:self.n + n] = 1 if cnt is None else cnt[order]
        self.n += n

    def _carry(self, ak, asg, ac):
        """The notes still sounding at carry_at, as this piece's synth pairs them (held copies + every note up to
        there, first in first out), handed to the piece starting there (song._carried): each at its own note-on with
        its count, all tracks together (over HELD_EXACT_MOST a key's copies were struck at its newest note-on: after
        Shinki's wall 800 M copies begun at 457.5 s struck again at every piece, a held chord instead of a decay).
        Port + channels with a pedal / all-notes-off, or a controller in the piece (notes left in the tracks' bytes),
        aren't carried: the next piece finds theirs track by track."""
        take = np.zeros(len(ak), np.int64)
        lim = (self.carry_at - self.base) << 16
        song = self.song
        at_f = song.frame(self.carry_at)
        for back in CARRY_ENDED_S:  # (notes ended this long before too: their release tails, 0.02 s early in walls)
            w = max(self.base, song.tick_at(max(0, at_f - int(back * RATE))))
            if _open_loop()(ak, asg, ac, len(ak), lim, (w - self.base) << 16, take) <= CARRY_ROWS_MOST:
                break
        pick = np.flatnonzero(take)
        carried = ~(self.pedal | ((self.bar_ch & 1) == 1))
        pick = pick[carried[(ak[pick] >> 8) & 255]]
        # (19 bytes a row: tick, port + channel x 128 + key, velocity (0 = an off), count)
        song._keep_carry({"at": self.carry_at, "f0": self.carry_f, "pcs": carried,
                          "tick": (ak[pick] >> 16) + self.base, "pk": ((ak[pick] >> 1) & 32767).astype(np.uint16),
                          "vel": asg[pick].copy(), "cnt": take[pick]})

    def carried(self, carry, floor):
        """The notes handed over by the piece before (_carry), into this piece (before held(): all before t_pre, in
        the order that piece's synth paired them): note-ons (vel > 0; older than floor = at floor) and offs."""
        n = len(carry["tick"])
        if not n:
            return
        pk, vel = carry["pk"].astype(np.int64), carry["vel"]
        pc = pk >> 7
        is_on = vel > 0
        on = ((self.bar_ch[pc] & 2) == 0) & is_on
        tick = np.where(is_on, np.maximum(carry["tick"], floor), carry["tick"])
        keys = ((tick - self.base) << 16) | (pk << 1) | on
        self.room(n)
        self.k[self.n:self.n + n] = keys
        self.s[self.n:self.n + n] = vel
        self.c[self.n:self.n + n] = carry["cnt"]
        self.n += n

    def clash_at(self, j, start, qs, vel, t_pre):
        """Whether track j's held copies (start ticks, channel x 256 + key, velocities) hold one on t_pre (or after)
        whose order there matters (call before held()). One on t_pre = struck at t_pre before the checkpoint there
        (Shinki 458 s: notes begun and ended at t_pre, a checkpoint between). Written in its track's head it comes
        after the tracks before it and before the rest's events at t_pre; counted, after every track's. The same
        then when: no controller of its port + channel at t_pre (those stay in the tracks), and among the counted
        notes of its port + channel + key at t_pre no note-on of another velocity (an order between note-ons decides
        which copy an off ends later; offs before ons: midi_piece.order_ok checks them), none at all on a channel
        kept in order (bar_ch bit 2: ons and offs there in the order put in)."""
        at = start >= t_pre
        if not at.any():
            return False
        if (start > t_pre).any():
            return True
        nc = int(self.song.newch[j])
        q = qs[at].astype(np.int64)
        pc = int(self.song.tport[j]) * 16 + (np.full(len(q), nc, np.int64) if nc >= 0 else q >> 8)
        if np.isin((t_pre << 8) | pc, self.bars).any():
            return True
        ak, asg, _ = self.acc
        lo = int(np.searchsorted(ak, (t_pre - self.base) << 16))
        hi = int(np.searchsorted(ak, (t_pre - self.base + 1) << 16))
        if lo == hi:
            return False
        there = {}  # port + channel + key -> velocities of its note-ons at t_pre (0 = an off, or kept in order)
        for k, s in zip((ak[lo:hi] & 0xFFFF).tolist(), asg[lo:hi].tolist()):
            there.setdefault(k >> 1, set()).add(s if k & 1 else -1)
        for p, key, v in zip(pc.tolist(), (q & 255).tolist(), np.maximum(vel[at], 1).tolist()):
            got = there.get((p << 8) | key)
            if got is None:
                continue
            if self.bar_ch[p] & 2 or (got - {-1, v}):
                return True
        return False

    def flush(self):
        """The batch handed to a sorting thread (sorted + folded), kept in order in the stack (oldest = earlier
        tracks first); merged down in acc (in pairs: Shinki's 457 s, 359 batches merged one by one took 50 s)."""
        if not self.n:
            return
        sort, _, _, _ = _counted_loops()
        n, k, s, c, bits = self.n, self.k, self.s, self.c, self.bits

        def job():
            m = sort(k, s, c, n, np.empty(n, np.int64), np.empty(n, np.uint8), np.empty(n, np.int64), bits)
            return k[:m].copy(), s[:m].copy(), c[:m].copy()
        # (PLAN E3: sorted on the other cores while the walk goes on; Shinki's 432 s, 137 batches = 9 s on one)
        self.stack.append(_sort_pool().submit(job))
        self.k = np.zeros(self.cap, np.int64)
        self.s = np.zeros(self.cap, np.uint8)
        self.c = np.ones(self.cap, np.int64)
        self.n = 0
        busy = [f for f in self.stack if not isinstance(f, tuple) and not f.done()]
        if len(busy) > 2 * SORT_WORKERS:  # (batches waiting to be sorted held at most this many: memory)
            busy[0].result()

    @property
    def acc(self):
        """Everything folded so far as one list: the sorted batches merged in pairs, neighbours only (the earlier
        tracks' runs first), each level's pairs at once on the other cores."""
        self.flush()
        parts = [f if isinstance(f, tuple) else f.result() for f in self.stack]
        _, merge, _, _ = _counted_loops()

        def two(a, b):
            (ak, asg, ac), (bk, bsg, bc) = a, b
            size = len(ak) + len(bk)
            ok, osg, oc = np.empty(size, np.int64), np.empty(size, np.uint8), np.empty(size, np.int64)
            got = merge(ak, asg, ac, len(ak), bk, bsg, bc, len(bk), ok, osg, oc)
            return ok[:got], osg[:got], oc[:got]
        while len(parts) > 1:
            pairs = [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
            last = [parts[-1]] if len(parts) % 2 else []
            parts = list(_sort_pool().map(lambda p: two(*p), pairs)) + last
            del pairs
        self.stack = parts
        return self.stack[0] if self.stack else (np.zeros(0, np.int64), np.zeros(0, np.uint8), np.zeros(0, np.int64))

    def tracks(self, origin):
        """The counted notes as track chunks, one a port, ticks from the piece's origin; let go of after. None
        when a tick's offs sorted before its ons would pair differently (the caller keeps the tracks' order)."""
        ak, asg, ac = self.acc
        self.k = self.s = self.c = None
        self.stack = []
        out = []
        if not len(ak):
            if self.carry_at is not None:  # (nothing sounding: an empty hand-over, the chain goes on)
                self._carry(ak, asg, ac)
            return out
        _, _, write, ok = _counted_loops()
        if not ok(ak, asg, ac, len(ak), self.bar_ch):
            return None
        if self.carry_at is not None:
            self._carry(ak, asg, ac)
        if self.dry:
            return out
        port_of = (ak >> 12) & 15
        for port in np.unique(port_of).tolist():
            mem = np.zeros(int((port_of == port).sum()) * 32 + 16, np.uint8)
            w, _ = write(ak, asg, ac, len(ak), port, self.base - origin, mem)
            body = b"\x00\xff\x21\x01" + bytes([port]) + mem[:w].tobytes() + b"\x00\xff\x2f\x00"
            out.append(b"MTrk" + len(body).to_bytes(4, "big") + body)
        return out


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
