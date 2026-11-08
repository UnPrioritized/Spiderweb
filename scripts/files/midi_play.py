"""The byte loops for playing a loaded MIDI (Load MIDI group D). Plain Python here; our own loops (native/loops) do
the very same in Rust (play_read / play_order / play_hist / play_emit), so both give identical results.

read   many tracks' channel events from their bytes, up to a tick (what playing needs)
programs  a track's program / bank state at a byte place (where playing starts: the instruments in effect there)
order  events of many tracks put in tick order (counting sort: each track's own order kept, earlier tracks first)
order_count / order_place  the same in halves, for a window read on several threads
stamp  Kestrel (GPU): events with the frame each sounds at, exact copies on a tick sent once
condense / stamp_pre  Kestrel (GPU): a part too dense to read live, read ahead with its copies collapsed / played
hist   how many note-ons each synth gets in one step, by velocity (the note budget's thinning)
emit   one step's events as raw MIDI bytes for each synth: thinned, muted and late notes left out together with
       their note-offs (a key's counter of offs still to skip), controllers to every synth of their channel"""

import numpy as np

def read(b, at, size, t1, tick, run, nxt, used, kn, out_t, out_m, out_k, n, base, sk, sk_i, sk_end, count, opened,
         slot):
    """Many tracks' whole channel events before tick t1 in one go (one Python call for hundreds of tracks: Shinki
    has ~800 busy ones in a slice of time). Track j's bytes = b[at[j]:at[j] + size[j]] (a track walk() read
    without BAD, from where it was), its tick / running status so far tick[j] / run[j] (moved on). Events into
    out_t (ticks) / out_m (status | data 1 << 8 | data 2 << 16) / out_k (kn[j], its track) from n. Program changes
    and bank select are kept (user 2026-10-25: the file's instruments play; their sounds are loaded before playing,
    play_loaded.preload). Left out: keys past 127 (a synth has 128), meta / sysex events. A track stops at its first
    event at or after t1 (nxt[j] = that tick, not read; else -1) or at one not wholly in its bytes; used[j] = its
    bytes read. Hidden events (Extract, midi_scan): track j's are sk[sk_i[j]:sk_end[j]] (byte places in the track,
    sk_i moved on), b[at[j]] = its byte base[j]. count: notes only counted, never kept (finding where playing
    starts, or stepping over a wall too big to read): opened[slot[j] x 2048 + channel x 128 + key] = notes sounding
    (an off with none sounding: nothing); key pressure left out too; the other events kept. Returns n."""
    for j in range(len(at)):
        p = at[j]
        end = at[j] + size[j]
        tk = tick[j]
        rn = run[j]
        nxt[j] = -1
        si = sk_i[j]
        while p < end:
            pos = base[j] + p - at[j]
            while si < sk_end[j] and sk[si] < pos:
                si += 1
            hid = si < sk_end[j] and sk[si] == pos
            q = p
            d = 0
            whole = True
            while True:
                if q >= end:
                    whole = False
                    break
                c = np.int64(b[q])
                q += 1
                d = (d << 7) | (c & 127)
                if c < 128:
                    break
            if whole and q >= end:
                whole = False
            if not whole:
                break
            t = tk + d
            if t >= t1:
                nxt[j] = t
                break
            s = np.int64(b[q])
            if s >= 128:
                q += 1
            else:
                s = rn
            if s < 0xF0:
                hi = s >> 4
                need = 1 if hi == 0xC or hi == 0xD else 2
                if q + need > end:
                    break
                if s >= 128:
                    rn = s
                a = np.int64(b[q])
                v = np.int64(b[q + 1]) if need == 2 else np.int64(0)
                keep = True
                if (hi == 0x8 or hi == 0x9 or hi == 0xA) and a >= 128:
                    keep = False
                if hid and (hi == 0x8 or hi == 0x9):
                    keep = False
                if count and keep and (hi == 0x8 or hi == 0x9 or hi == 0xA):
                    if hi != 0xA:
                        ln = slot[j] * 2048 + (s & 15) * 128 + (a & 127)
                        if hi == 0x9 and v > 0:
                            opened[ln] += 1
                        elif opened[ln] > 0:
                            opened[ln] -= 1
                    keep = False
                if keep:
                    out_t[n] = t
                    out_m[n] = s | (a & 127) << 8 | (v & 127) << 16
                    out_k[n] = kn[j]
                    n += 1
                q += need
            else:
                if s == 0xFF:
                    if q >= end:
                        break
                    q += 1
                ln = 0
                whole = True
                while True:
                    if q >= end:
                        whole = False
                        break
                    c = np.int64(b[q])
                    q += 1
                    ln = (ln << 7) | (c & 127)
                    if c < 128:
                        break
                q += ln
                if not whole or q > end:
                    break
            tk = t
            p = q
        tick[j] = tk
        run[j] = rn
        used[j] = p - at[j]
        sk_i[j] = si
    return n


PS_TICK, PS_RUN, PS_BANK, PS_PTICK, PS_PVAL, PS_BTICK = 0, 1, 2, 18, 34, 50  # programs(): st, as midi_scan's
PS_SIZE = 66  # C_BANK / C_PTICK / C_PVAL / C_BTICK per channel


def programs(b, p, end, st):
    """The whole events in b[p:end] (a track's bytes from its start, piece after piece: st carries on) walked for
    the instruments: each channel's bank select now (st[PS_BTICK + channel] = its last one's tick + 1) and its last
    program change (st[PS_PTICK + channel] = its tick + 1, 0 = none; st[PS_PVAL + channel] = program | bank << 7).
    Returns where it stopped (an event not wholly in b:
    call again with more from there)."""
    tk = st[PS_TICK]
    rn = st[PS_RUN]
    while p < end:
        q = p
        d = 0
        whole = True
        while True:
            if q >= end:
                whole = False
                break
            c = np.int64(b[q])
            q += 1
            d = (d << 7) | (c & 127)
            if c < 128:
                break
        if not whole or q >= end:
            break
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        else:
            s = rn
        if s < 0xF0:
            hi = s >> 4
            need = 1 if hi == 0xC or hi == 0xD else 2
            if q + need > end:
                break
            if s >= 128:
                rn = s
            ch = s & 15
            a = np.int64(b[q]) & 127
            if hi == 0xB and need == 2:
                v = np.int64(b[q + 1]) & 127
                if a == 0:
                    st[PS_BANK + ch] = (st[PS_BANK + ch] & ~np.int64(127)) | v
                    st[PS_BTICK + ch] = tk + d + 1
                elif a == 32:
                    st[PS_BANK + ch] = (st[PS_BANK + ch] & 127) | (v << 7)
                    st[PS_BTICK + ch] = tk + d + 1
            elif hi == 0xC:
                st[PS_PTICK + ch] = tk + d + 1
                st[PS_PVAL + ch] = a | (st[PS_BANK + ch] << 7)
            q += need
        else:
            if s == 0xFF:
                if q >= end:
                    break
                q += 1
            ln = 0
            whole = True
            while True:
                if q >= end:
                    whole = False
                    break
                c = np.int64(b[q])
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            q += ln
            if not whole or q > end:
                break
        tk += d
        p = q
    st[PS_TICK] = tk
    st[PS_RUN] = rn
    return p


def order(ticks, ms, ks, base, span, out_t, out_m, out_k):
    """The events (ticks each base .. base + span - 1, messages, tracks) into out_* in tick order, ties in their
    order (a counting sort: many tracks' events put together in one go, written straight to their places)."""
    count = np.zeros(span + 1, np.int64)
    for i in range(len(ticks)):
        count[ticks[i] - base + 1] += 1
    for k in range(span):
        count[k + 1] += count[k]
    for i in range(len(ticks)):
        k = ticks[i] - base
        j = count[k]
        out_t[j] = ticks[i]
        out_m[j] = ms[i]
        out_k[j] = ks[i]
        count[k] = j + 1


def order_count(ticks, base, count):
    """order's first half for one part of a window read on several threads: count[tick - base] += 1."""
    for i in range(len(ticks)):
        count[ticks[i] - base] += 1


def order_place(ticks, ms, ks, base, at, out_t, out_m, out_k):
    """order's second half for one part: each event to at[its tick - base] (moved on), its places worked out from
    every part's order_count (the parts side by side; earlier parts first on a tick)."""
    for i in range(len(ticks)):
        k = ticks[i] - base
        j = at[k]
        out_t[j] = ticks[i]
        out_m[j] = ms[i]
        out_k[j] = ks[i]
        at[k] = j + 1


def hist(ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, h):
    """h[synth, velocity] += the note-ons of events i0..i1 that would be played (not muted, not late). muted[track] =
    its channels muted, one bit each (every bit: the whole track; a folder's sub-row: one). ports < 0: each port its
    own synth (r = port: Maestro's ports)."""
    exact = ports < 0
    per = 1 if exact or ports >= synths else synths // ports
    for i in range(i0, i1):
        m = ms[i]
        if (m & 0xF0) != 0x90:
            continue
        v = (m >> 16) & 127
        if v == 0 or (muted[trk[i]] >> (m & 15)) & 1 or ticks[i] < t_late:
            continue
        port = tport[trk[i]]  # (its synth: as emit's)
        ch = m & 15
        if exact:
            r = port
        else:
            r = (port + ch) % synths if ports >= synths else \
                (port + ch) % ports * per + (((m >> 8) & 127) * 5 + ch * 3) % per
        h[r, v] += 1


COPY_TABLE = 1 << 20  # (stamp's table of note-ons on the tick being read: power of two)
COPY_PROBES = 64  # ... places looked at for one; none free = sent on its own


def stamp(ms, trk, i0, i1, ticks, muted, tport, mt, ms_, mspt, base, rate, least, fr, out, tr, counts, cp,
          tab_key, tab_gen, tab_at, head, tail, val, nxt, st):
    """Kestrel (GPU): events i0..i1 into fr / out / tr (the frame each sounds at, the message with its port in the top
    byte, what the note budget shares by: channel + port) from 0; returns how many. Frame = base + rate x song
    seconds (tempo map mt ticks / ms_ seconds / mspt seconds a tick, playback.TimeMap's). A note-on of a muted track, or for a frame before `least` (too late:
    the sound is made already), is left out with ITS note-off (the line: head ... st, see Line); other late events go
    at `least` (a whole block's start); everything else goes: Kestrel's own note budget chooses. counts[0] +=
    note-ons wanted, counts[1] += note-ons sent.
    EXACT COPIES (same tick, message and port; Shinki's dense part: 4.5 note-ons for each different one) go ONCE,
    cp[k] = how many (kb_push_copies: their loudness adds as amplitudes); the note sent ends at the LAST of its
    copies' note-offs (user 2026-11-05: the first one cut the longer copies short), the others are left out (the
    line: the sent note's group, Line). tab_* = a table over the tick being read (tab_at = place in out | the
    group's node << 32), counts[2] = its generation. The line needs room for a node per note-on (Line.room)."""
    n = 0
    j = 0
    while j + 1 < len(mt) and mt[j + 1] <= ticks[i0]:
        j += 1
    last_t = -1
    for i in range(i0, i1):
        t = ticks[i]
        if t != last_t:
            last_t = t
            counts[2] += 1  # (a new tick: the table starts empty)
        while j + 1 < len(mt) and mt[j + 1] <= t:
            j += 1
        f = base + rate * (ms_[j] + (t - mt[j]) * mspt[j])
        f = 0.0 if f < 0.0 else f
        m = ms[i]
        hi = (m & 0xFF) >> 4
        port = tport[trk[i]]
        if hi == 0x8 or hi == 0x9:
            # (port % 16 as Kestrel has it, in every skip / opened of its path: hunt 2026-10-27, past 16 ports a part
            # read ahead and the live reading counted under different ports)
            q = ((port % 16) * 16 + (m & 15)) * 128 + ((m >> 8) & 127)
            if hi == 0x9 and (m >> 16) & 127 > 0:
                d = 1  # (left out)
                hh = -1  # (its place in the copy table)
                # (muted = not wanted: never counted as thinned, it showed "Overloaded", hunt 2026-10-27)
                if not (muted[trk[i]] >> (m & 15)) & 1:
                    counts[0] += 1
                    if f >= least:
                        counts[1] += 1
                        key = np.uint32(m & 0xFFFFFF) | np.uint32(port % 16) << np.uint32(24)
                        h = (int(key) * 2654435761) & (COPY_TABLE - 1)
                        found = -1
                        for _ in range(COPY_PROBES):
                            if tab_gen[h] != counts[2]:
                                tab_gen[h] = counts[2]
                                tab_key[h] = key
                                hh = h
                                break
                            if tab_key[h] == key:
                                found = tab_at[h]
                                hh = h
                                break
                            h = (h + 1) & (COPY_TABLE - 1)
                        # (the sent one's group still in this key's line: an off of this tick may have ended it,
                        # its node gone back to the free nodes; else this one is sent as a note of its own)
                        if found >= 0 and val[found >> 32] < 0:
                            w = found >> 32
                            for _ in range(COPY_PROBES):
                                if w == tail[q] or w < 0:
                                    break
                                w = nxt[w]
                            if w >= 0 and w == tail[q]:
                                cp[found & 0xFFFFFFFF] += 1  # (one more of the sent note's group: its off waits)
                                val[found >> 32] -= 1
                                continue
                        d = -1
                # (at the back of its key's line: Line; a sent note = a group of its own)
                b = tail[q]
                if b >= 0 and d > 0 and val[b] > 0:
                    val[b] += d
                else:
                    k = st[0]
                    if k >= 0:
                        st[0] = nxt[k]
                    else:
                        k = st[2]
                        st[2] += 1
                    val[k] = d
                    nxt[k] = -1
                    if b >= 0:
                        nxt[b] = k
                    else:
                        head[q] = k
                    tail[q] = k
                    if d < 0 and hh >= 0:
                        tab_at[hh] = np.int64(n) | (np.int64(k) << 32)
                if d > 0:
                    continue
                st[3] += 1
            else:
                # (ends its key's oldest note: left out = this off too, a sent note's group only at its last one;
                # nothing there = it goes, ending nothing)
                k = head[q]
                if k >= 0:
                    v = val[k]
                    val[k] = v - 1 if v > 0 else v + 1
                    if val[k] == 0:
                        head[q] = nxt[k]
                        if nxt[k] < 0:
                            tail[q] = -1
                        nxt[k] = st[0]
                        st[0] = k
                    if v > 0 or v < -1:
                        continue
                    st[3] -= 1
        # (a late note-off / controller: at `least`, the first block not made yet; for a frame made already it went
        # into the block being made with the ones before it and that block no longer matched its ranking)
        fr[n] = np.uint64((least if f < least else f) + 0.5)
        out[n] = np.uint32(m & 0xFFFFFF) | np.uint32(port % 16) << np.uint32(24)
        # (Kestrel's note budget shares by CHANNEL + PORT, not the file's track: user 2026-10-27 took my pick.
        # Shared by Shinki's 2,788 tracks, every track kept 1-2 notes a block and its stacked lines lost their
        # loudness: the loudest 12 keys 7.5-9.3 dB off the user's render, by channel + port 1.5-5.8)
        tr[n] = (m & 15) | (port % 16) << 4
        cp[n] = 1
        n += 1
    return n


def condense(ts, ms, ks, tport, n, out_t, out_m, out_tr, out_cp, tab_key, tab_gen, tab_at, counts, last_on):
    """Kestrel (GPU)'s read-ahead of a part too dense to read live (files/play_dense.py): events in tick order into
    out_* from n (tick, message with its port in the top byte, the note budget's channel + port, copies), the EXACT
    COPIES collapsed as stamp does (cp = how many; nothing muted or late here). Note-offs alike on one tick collapse
    too, the copies' ones among them (cp = how many; a wall's 900 M note ends in Shinki, beat 1156, would stay 900 M
    events), never across a note-on of that key (last_on[q] = where its latest one went, -1 at first): stamp_pre's
    line hands out the ones of notes sent. Called piece after piece (one giant tick in groups of tracks too):
    counts[3] = the tick read last, counts[2] = the table's generation, counts[0] += note-ons read. Returns n."""
    for i in range(len(ts)):
        t = ts[i]
        if t != counts[3]:
            counts[3] = t
            counts[2] += 1
        m = ms[i]
        hi = (m & 0xFF) >> 4
        port = tport[ks[i]]
        if hi == 0x8 or hi == 0x9:
            q = ((port % 16) * 16 + (m & 15)) * 128 + ((m >> 8) & 127)
            if hi == 0x9 and (m >> 16) & 127 > 0:
                counts[0] += 1
                key = np.uint32(m & 0xFFFFFF) | np.uint32(port % 16) << np.uint32(24)
                h = (int(key) * 2654435761) & (COPY_TABLE - 1)
                found = -1
                for _ in range(COPY_PROBES):
                    if tab_gen[h] != counts[2]:
                        tab_gen[h] = counts[2]
                        tab_key[h] = key
                        tab_at[h] = n
                        break
                    if tab_key[h] == key:
                        found = tab_at[h]
                        break
                    h = (h + 1) & (COPY_TABLE - 1)
                if found >= 0:
                    out_cp[found] += 1
                    continue
                last_on[q] = n
            else:  # (offs alike on its tick collapsed too, never past a note-on of the key)
                key = np.uint32(m & 0xFFFFFF) | np.uint32(port % 16) << np.uint32(24)
                h = (int(key) * 2654435761) & (COPY_TABLE - 1)
                found = -1
                for _ in range(COPY_PROBES):
                    if tab_gen[h] != counts[2]:
                        tab_gen[h] = counts[2]
                        tab_key[h] = key
                        tab_at[h] = n
                        break
                    if tab_key[h] == key:
                        found = tab_at[h]
                        break
                    h = (h + 1) & (COPY_TABLE - 1)
                if found >= 0 and found > last_on[q]:
                    out_cp[found] += 1
                    continue
                if found >= 0:  # (a note-on came between: this off starts a new run)
                    tab_at[h] = n
        out_t[n] = t
        out_m[n] = np.uint32(m & 0xFFFFFF) | np.uint32(port % 16) << np.uint32(24)
        out_tr[n] = (m & 15) | (port % 16) << 4
        out_cp[n] = 1
        n += 1
    return n


def stamp_pre(ts, ms, trs, cps, t0, i0, i1, mt, ms_, mspt, base, rate, least, fr, out, tr, cp, counts, dry,
              head, tail, val, nxt, st):
    """stamp for a part read ahead (condense's arrays, ticks from t0): events i0..i1 into fr / out / tr / cp from 0,
    returns how many (room: pre_room). A note-on for a frame before `least` is left out with its offs (the line, as
    stamp); other late events go at `least`. dry: nothing written, every note-on left out (the line then holds the
    notes sounding where playing starts inside the part). counts[0] / [1] += note-ons wanted / sent (copies
    counted). A run of cps[i] offs alike ends that many of its key's oldest notes: one off goes for each sent note
    whose group (it + its copies, Line) ends there."""
    n = 0
    j = 0
    while j + 1 < len(mt) and mt[j + 1] <= ts[i0] + t0:
        j += 1
    for i in range(i0, i1):
        t = ts[i] + t0
        while j + 1 < len(mt) and mt[j + 1] <= t:
            j += 1
        f = base + rate * (ms_[j] + (t - mt[j]) * mspt[j])
        f = 0.0 if f < 0.0 else f
        m = ms[i]
        hi = (m & 0xFF) >> 4
        if hi == 0x8 or hi == 0x9:
            q = (((m >> 24) & 15) * 16 + (m & 15)) * 128 + ((m >> 8) & 127)
            c = np.int64(cps[i])
            if hi == 0x9 and (m >> 16) & 127 > 0:
                counts[0] += c
                sent = not dry and f >= least
                if sent:
                    counts[1] += c
                    st[3] += 1
                # (at the back of its key's line, Line: sent = one group of c, its off at the last of theirs)
                d = -c if sent else c
                b = tail[q]
                if b >= 0 and d > 0 and val[b] > 0:
                    val[b] += d
                else:
                    k = st[0]
                    if k >= 0:
                        st[0] = nxt[k]
                    else:
                        k = st[2]
                        st[2] += 1
                    val[k] = d
                    nxt[k] = -1
                    if b >= 0:
                        nxt[b] = k
                    else:
                        head[q] = k
                    tail[q] = k
                if not sent:
                    continue
            else:  # (c offs alike: each ends its key's oldest note; one goes for each sent group ended)
                s = 0
                while c > 0 and head[q] >= 0:
                    k = head[q]
                    v = val[k]
                    if v > 0:
                        take = v if v < c else c
                        val[k] = v - take
                    else:
                        take = -v if -v < c else c
                        val[k] = v + take
                        if val[k] == 0:
                            s += 1
                    c -= take
                    if val[k] == 0:
                        head[q] = nxt[k]
                        if nxt[k] < 0:
                            tail[q] = -1
                        nxt[k] = st[0]
                        st[0] = k
                st[3] -= s
                if s == 0 or dry:
                    continue
                g = np.uint64((least if f < least else f) + 0.5)
                for _ in range(s):
                    fr[n] = g
                    out[n] = m
                    tr[n] = trs[i]
                    cp[n] = 1
                    n += 1
                continue
        if dry:
            continue
        fr[n] = np.uint64((least if f < least else f) + 0.5)
        out[n] = m
        tr[n] = trs[i]
        cp[n] = cps[i]
        n += 1
    return n


OPEN = 16 * 16 * 128  # (the line's places: port % 16 x channel x key)


class Line:
    """Kestrel (GPU)'s note-off line. Kestrel ends a key's notes OLDEST FIRST (port % 16 x channel x key, OPEN
    places). The feeder leaves notes out (muted, too late, an exact copy sent as one) and must leave out exactly
    THEIR note-offs: leaving out the key's next ones (2026-10-27) ended the wrong notes, in Shinki's build-up ~40,000
    wait on a key, the older ones rang on (beats 834-846: 3.6x the notes sounding, its fade 1.3 s late; caught by
    dev/harness/app_check.py). So each place keeps the file's notes in order as runs, one a node of a linked list:
    val > 0 = that many left out; < 0 = stamp / stamp_pre: ONE note sent and its exact copies, -val of them (its off
    goes at the last of their offs: the longest copy, user 2026-11-05), emit: that many sent; an off takes the
    oldest. head / tail per place, nxt per node (-1 = none); st = [first free node, -, next node never used, notes
    sent and not ended]. arrays() = stamp's / stamp_pre's last 5 arguments; room(n) before a call adding up to n
    nodes (one per note-on)."""

    def __init__(self, nodes=1 << 16, places=OPEN):
        """places: OPEN for Kestrel; Built-in BASSMIDI's emit = SKIP's (port % 64 x channel x key)."""
        self.places = places
        self.head = np.full(places, -1, np.int64)
        self.tail = np.full(places, -1, np.int64)
        self.val = np.zeros(nodes, np.int64)
        self.nxt = np.full(nodes, -1, np.int64)
        self.st = np.array([-1, 0, 0, 0], np.int64)

    def arrays(self):
        return self.head, self.tail, self.val, self.nxt, self.st

    def room(self, more):
        if self.st[2] + more > len(self.val):
            size = max(2 * len(self.val), int(self.st[2] + more))
            self.val = np.concatenate([self.val, np.zeros(size - len(self.val), np.int64)])
            self.nxt = np.concatenate([self.nxt, np.full(size - len(self.nxt), -1, np.int64)])

    def reset(self, skip=None):
        """Every line empty (every sound stopped); skip (SKIP places, port x channel x key) = notes left out first."""
        self.head[:] = -1
        self.tail[:] = -1
        self.st[:] = (-1, 0, 0, 0)
        if skip is not None:
            self.add(skip)

    def add(self, skip):
        """skip[port x channel x key] notes left out, at the back of their lines."""
        c = np.asarray(skip, np.int64).reshape(-1, self.places).sum(0)
        qs = np.flatnonzero(c > 0)
        self.room(len(qs))
        head, tail, val, nxt, st = self.arrays()
        for q in qs.tolist():
            b = int(tail[q])
            if b >= 0 and val[b] > 0:
                val[b] += c[q]
                continue
            k = int(st[2])
            st[2] += 1
            val[k], nxt[k] = c[q], -1
            if b >= 0:
                nxt[b] = k
            else:
                head[q] = k
            tail[q] = k

    def left_out(self):
        """Notes waiting in the lines that were left out, per SKIP place (port % 16 x channel x key)."""
        out = np.zeros(OPEN * 4, np.int64)
        for q in np.flatnonzero(self.head >= 0).tolist():
            k = int(self.head[q])
            while k >= 0:
                if self.val[k] > 0:
                    out[q] += self.val[k]
                k = int(self.nxt[k])
        return out


def pre_room(ms, cps, i0, i1, st):
    """Room stamp_pre needs for events i0..i1: each, plus each run of offs' extra ones (never more than the notes
    sent and sounding, st[3]: a play started after a wall gets its billion note ends as nothing)."""
    m = ms[i0:i1]
    hi = (m & 0xF0) >> 4
    off = (hi == 0x8) | ((hi == 0x9) & (((m >> 16) & 127) == 0))
    on = (hi == 0x9) & ~off
    return int(i1 - i0 + min(np.int64(cps[i0:i1][off].astype(np.int64).sum()), int(st[3]) + int(on.sum())))


def ons_in(ms, i0, i1):
    """Note-ons among events i0..i1 (the line's room for stamp / emit: a node each at most)."""
    m = ms[i0:i1]
    return int((((m & 0xF0) == 0x90) & (((m >> 16) & 127) > 0)).sum())


def pre_nodes(ms, i0, i1):
    """The line's room for stamp_pre over events i0..i1: two nodes a note-on, one sent + its copies left out (room
    for one wrote past the line's end: the app closed without a word inside Shinki's wall, 2026-10-28)."""
    return 2 * ons_in(ms, i0, i1)


def emit(ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, thr, room, out, lens, three,
         head, tail, val, nxt, st):
    """Events i0..i1 as raw MIDI bytes into out[synth] from lens[synth]. A note-on goes out when not muted, not late
    (tick before t_late) and loud enough: velocity >= thr[synth], or one below it while room[synth] lasts (the note
    budget, hist's counts); else it's left out with ITS note-off: the line (head ... st, Line with SKIP places: port %
    64 x channel x key) keeps each key's notes in file order, an off takes the oldest. Was a counter ("the key's NEXT
    off goes"): kept notes rang on past their own off, Shinki bar 209 91 % of them (dev/scratch/bm_offs.py,
    2026-10-28). Needs room for a node per note-on (Line.room).
    Controllers / bends / channel pressure go to every synth of their channel's block. three: every message 3 bytes (a
    device that takes whole messages). ports < 0: each port its own synth, r = the port (Maestro's ports, tport
    under synths). Stops before an event that wouldn't fit; returns where it stopped."""
    cap = out.shape[1]
    exact = ports < 0
    per = 1 if exact or ports >= synths else synths // ports
    for i in range(i0, i1):
        m = ms[i]
        s = m & 0xFF
        hi = s >> 4
        ch = s & 15
        port = tport[trk[i]]
        size = 2 if (hi == 0xC or hi == 0xD) and not three else 3
        if hi == 0x8 or hi == 0x9 or hi == 0xA:
            key = (m >> 8) & 127
            # (the synth: the synths in blocks, one per port; each channel's block turned by its number, so a busy
            # port's channels reach every block, two ports' same channel never share a synth; a channel's keys
            # spread over its block; a channel + key always the same synth, so a note-off finds its note)
            if exact:
                r = port
            else:
                r = (port + ch) % synths if ports >= synths else (port + ch) % ports * per + (key * 5 + ch * 3) % per
            if lens[r] + 3 > cap:
                return i
            q = ((port % 64) * 16 + ch) * 128 + key
            v = (m >> 16) & 127
            if hi == 0x9 and v > 0:
                go = not (muted[trk[i]] >> ch) & 1 and ticks[i] >= t_late
                if go and v < thr[r]:
                    go = v == thr[r] - 1 and room[r] > 0
                    if go:
                        room[r] -= 1
                d = -1 if go else 1
                b = tail[q]
                if b >= 0 and (val[b] > 0) == (d > 0):
                    val[b] += d
                else:
                    k = st[0]
                    if k >= 0:
                        st[0] = nxt[k]
                    else:
                        k = st[2]
                        st[2] += 1
                    val[k] = d
                    nxt[k] = -1
                    if b >= 0:
                        nxt[b] = k
                    else:
                        head[q] = k
                    tail[q] = k
                if not go:
                    continue
            elif hi != 0xA:
                # (ends its key's oldest note: left out = this off too; nothing there = it goes)
                k = head[q]
                if k >= 0:
                    v = val[k]
                    val[k] = v - 1 if v > 0 else v + 1
                    if val[k] == 0:
                        head[q] = nxt[k]
                        if nxt[k] < 0:
                            tail[q] = -1
                        nxt[k] = st[0]
                        st[0] = k
                    if v > 0:
                        continue
            o = lens[r]
            out[r, o] = s
            out[r, o + 1] = (m >> 8) & 127
            out[r, o + 2] = (m >> 16) & 127
            lens[r] = o + 3
        else:
            if exact:
                first = port
            else:
                first = (port + ch) % ports * per if ports < synths else (port + ch) % synths
            for r in range(first, first + per):
                if lens[r] + size > cap:
                    return i
            for r in range(first, first + per):
                o = lens[r]
                out[r, o] = s
                out[r, o + 1] = (m >> 8) & 127
                if size == 3:
                    out[r, o + 2] = (m >> 16) & 127
                lens[r] = o + size
    return i1
