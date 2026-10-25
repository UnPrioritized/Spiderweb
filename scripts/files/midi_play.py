"""The byte loops for playing a loaded MIDI (Load MIDI group D). Plain Python here; files/fastloops.py compiles the
very same functions with Numba (play_read / play_order / play_hist / play_emit), so both give identical results.

read   many tracks' channel events from their bytes, up to a tick (what playing needs)
programs  a track's program / bank state at a byte place (where playing starts: the instruments in effect there)
order  events of many tracks put in tick order (counting sort: each track's own order kept, earlier tracks first)
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


PS_TICK, PS_RUN, PS_BANK, PS_PTICK, PS_PVAL = 0, 1, 2, 18, 34  # programs(): st, as midi_scan's C_BANK / C_PTICK /
PS_SIZE = 50  # C_PVAL per channel


def programs(b, p, end, st):
    """The whole events in b[p:end] (a track's bytes from its start, piece after piece: st carries on) walked for
    the instruments: each channel's bank select now and its last program change (st[PS_PTICK + channel] = its tick
    + 1, 0 = none; st[PS_PVAL + channel] = program | bank << 7). Returns where it stopped (an event not wholly in b:
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
                elif a == 32:
                    st[PS_BANK + ch] = (st[PS_BANK + ch] & 127) | (v << 7)
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


def hist(ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, h):
    """h[synth, velocity] += the note-ons of events i0..i1 that would be played (not muted, not late)."""
    per = 1 if ports >= synths else synths // ports
    for i in range(i0, i1):
        m = ms[i]
        if (m & 0xF0) != 0x90:
            continue
        v = (m >> 16) & 127
        if v == 0 or muted[trk[i]] or ticks[i] < t_late:
            continue
        port = tport[trk[i]]  # (its synth: as emit's)
        ch = m & 15
        r = (port + ch) % synths if ports >= synths else (port + ch) % ports * per + (((m >> 8) & 127) * 5 + ch * 3) % per
        h[r, v] += 1


def emit(ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, thr, room, skip, out, lens, three):
    """Events i0..i1 as raw MIDI bytes into out[synth] from lens[synth]. A note-on goes out when not muted, not late
    (tick before t_late) and loud enough: velocity >= thr[synth], or one below it while room[synth] lasts (the note
    budget, hist's counts); else its key's skip counter grows and the next note-off there is left out with it.
    Controllers / bends / channel pressure go to every synth of their channel's block. three: every message 3 bytes (a
    device that takes whole messages). Stops before an event that wouldn't fit; returns where it stopped."""
    cap = out.shape[1]
    per = 1 if ports >= synths else synths // ports
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
            r = (port + ch) % synths if ports >= synths else (port + ch) % ports * per + (key * 5 + ch * 3) % per
            if lens[r] + 3 > cap:
                return i
            q = ((port % 64) * 16 + ch) * 128 + key
            v = (m >> 16) & 127
            if hi == 0x9 and v > 0:
                go = not muted[trk[i]] and ticks[i] >= t_late
                if go and v < thr[r]:
                    go = v == thr[r] - 1 and room[r] > 0
                    if go:
                        room[r] -= 1
                if not go:
                    skip[q] += 1
                    continue
            elif hi != 0xA:
                if skip[q] > 0:
                    skip[q] -= 1
                    continue
            o = lens[r]
            out[r, o] = s
            out[r, o + 1] = (m >> 8) & 127
            out[r, o + 2] = (m >> 16) & 127
            lens[r] = o + 3
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
