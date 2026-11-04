"""Rendered ahead's pieces of a LOADED MIDI (PLAN.md L0 / L1): a track's piece is its own bytes, COPIED from a
checkpoint, never read into notes and written again (measured 2026-11-03: ~5x quicker; Shinki 4.5 s of music made
in 2 s instead of 9.3). Plain Python that files/fastloops.py compiles (piece_track; the same function both ways).

piece_track walks a track's bytes from a checkpoint. Before t_pre it only counts: which notes sound there (cnt,
channel x 256 + key, and the newest one's velocity / tick). From the first event at / after t_pre up to t_on (the
piece's end) it copies the events into out, then up to the first past t_end only the offs of notes still open,
changing only what playing would change:
- hidden events (Extract: byte places sk) and keys past 127 (a synth has 128; live play leaves them out) left out,
  their wait added to the next event's;
- channels muted in the track list (muted: a bit each) left out;
- Track Properties' channel (newch >= 0): every channel event moved onto it;
- the file's own tempo / port events (FF 51 / FF 21) made plain text events (FF 01, same length): the piece's tempo
  track carries the tempo as heard, the track's port is written at its start;
- the first copied event's wait counted from `last` (the piece writes its held notes / controllers before it), a
  status byte written wherever the original relied on one no longer there.
End of Track is not copied (the caller ends the track)."""

import numpy as np

FAR = 1 << 62
QS = 16 * 256  # channel x key (cnt's places)


def piece_track(b, base, tick, run, t_pre, t_end, sk, cnt, vel, ontk, muted, newch, last, out, t_on, opn,
                port, base_t, bars, bar_ch, ev_key, ev_sig, nev):
    """b = a track's bytes from a checkpoint at byte place base in the track (tick, running status there). Returns
    (bytes written to out, the tick of the last event written (last when none), bad = 1 when the track's bytes end
    or break before t_end). out must hold len(b) + 4 x hidden events + 16 bytes.
    Past t_on (the piece's end) only the note-offs of notes still open are copied, and the walk stops once none is
    (PLAN B2: the wall's 0.3 s past each part was ~90 % of what Merlin got; Merlin only needs those offs, measured
    -115..-130 dB vs copying everything). opn = QS scratch counts (the open notes per place).
    COUNTED (PLAN D2, Merlin pieces; len(ev_key) > 0): the copied notes go to ev_key / ev_sig at nev[0] instead
    (key = (tick - base_t) << 16 | port << 12 | channel << 8 | key << 1 | on, sig = velocity, 0 = off; ev_key holds
    len(b) // 3 more; `on` sorts a tick's offs before its ons where bar_ch has bit 2 clear: see order_ok), except on
    a port + channel with a controller at that tick (bars: sorted tick << 8 | port << 4 | channel, bar_ch[port << 4
    | channel] bit 1 = has any): there the order between tracks matters, they stay. nev[1] = 1 when a SysEx is
    copied (can't be ordered against: the caller makes the piece uncounted)."""
    n = len(b)
    p = 0
    w = 0
    si = 0
    nsk = len(sk)
    counted = len(ev_key) > 0
    nb = len(bars)
    copying = False
    nopen = 0
    wrote = -1  # (the status byte the copy's running status is, -1 = none: the next channel event writes one)
    while p < n:
        pos = base + p
        while si < nsk and sk[si] < pos:
            si += 1
        hid = si < nsk and sk[si] == pos
        q = p
        d = 0
        while q < n:
            c = np.int64(b[q])
            q += 1
            d = (d << 7) | (c & 127)
            if c < 128:
                break
        t = tick + d
        if t > t_end:
            return w, last, 0
        if q >= n:
            return w, last, 1
        if not copying and t >= t_pre:
            copying = True
            nopen = 0
            for i in range(len(cnt)):  # (the notes sounding at t_pre are open)
                opn[i] = cnt[i]
                nopen += cnt[i]
        if copying and t > t_on and nopen <= 0:
            return w, last, 0
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        elif run == 0:
            return w, last, 1
        else:
            s = run
        if s < 0xF0:
            hi = s >> 4
            need = 1 if hi == 0xC or hi == 0xD else 2
            if q + need > n:
                return w, last, 1
            run = s
            ch = s & 15
            skip = hid or (muted >> ch) & 1 == 1
            if (hi == 0x9 or hi == 0x8 or hi == 0xA) and np.int64(b[q]) > 127:
                skip = True
            if copying and not skip:  # (open notes counted; past t_on only their offs kept)
                is_on = hi == 0x9 and np.int64(b[q + 1]) > 0
                if is_on or hi == 0x8 or hi == 0x9:
                    k = ch * 256 + np.int64(b[q])
                    if t > t_on and (is_on or opn[k] <= 0):
                        skip = True
                    elif is_on:
                        opn[k] += 1
                        nopen += 1
                    elif opn[k] > 0:
                        opn[k] -= 1
                        nopen -= 1
                elif t > t_on:
                    skip = True
                if counted and not skip and (hi == 0x9 or hi == 0x8):  # (to the counted notes, PLAN D2)
                    och = newch if newch >= 0 else ch
                    pc = port * 16 + och
                    stay = False
                    if bar_ch[pc] & 1:
                        bk = (t << 8) | pc
                        lo = 0
                        up = nb
                        while lo < up:
                            mid = (lo + up) >> 1
                            if bars[mid] < bk:
                                lo = mid + 1
                            else:
                                up = mid
                        stay = lo < nb and bars[lo] == bk
                    if not stay:
                        e = nev[0]
                        ev_key[e] = (((t - base_t) << 16) | (pc << 8) | (np.int64(b[q]) << 1)
                                     | (1 if is_on and bar_ch[pc] & 2 == 0 else 0))
                        ev_sig[e] = np.int64(b[q + 1]) if is_on else 0
                        nev[0] = e + 1
                        skip = True
            if not copying:
                if not skip and (hi == 0x9 or hi == 0x8):
                    k = ch * 256 + np.int64(b[q])
                    v = np.int64(b[q + 1])
                    if hi == 0x9 and v > 0:
                        cnt[k] += 1
                        vel[k] = v
                        ontk[k] = t
                    elif cnt[k] > 0:
                        cnt[k] -= 1
            elif not skip:
                if newch >= 0:
                    s = (s & 0xF0) | newch
                # the wait from the last event written
                dd = t - last
                m = 0
                tmp = dd
                while True:
                    m += 1
                    tmp >>= 7
                    if tmp == 0:
                        break
                for i in range(m - 1, -1, -1):
                    byte = (dd >> (7 * i)) & 127
                    out[w] = byte | (0x80 if i > 0 else 0)
                    w += 1
                if s != wrote:
                    out[w] = s
                    w += 1
                    wrote = s
                for i in range(need):
                    out[w] = b[q + i]
                    w += 1
                last = t
            q += need
        elif s == 0xFF or s == 0xF0 or s == 0xF7:
            start = q
            mt = -1
            if s == 0xFF:
                if q >= n:
                    return w, last, 1
                mt = np.int64(b[q])
                q += 1
            ln = 0
            while q < n:
                c = np.int64(b[q])
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            if q + ln > n:
                return w, last, 1
            if mt == 0x2F:
                return w, last, 0
            if copying and not hid and t <= t_on:
                if counted and s != 0xFF:
                    nev[1] = 1
                dd = t - last
                m = 0
                tmp = dd
                while True:
                    m += 1
                    tmp >>= 7
                    if tmp == 0:
                        break
                for i in range(m - 1, -1, -1):
                    out[w] = ((dd >> (7 * i)) & 127) | (0x80 if i > 0 else 0)
                    w += 1
                out[w] = s
                w += 1
                for i in range(start, q + ln):
                    out[w] = b[i]
                    w += 1
                if mt == 0x51 or mt == 0x21:  # (tempo / port: plain text of the same length)
                    out[w - (q + ln - start)] = 0x01
                wrote = -1  # (after a meta / SysEx the next channel event writes its status: any reader agrees)
                last = t
            q += ln
        else:
            return w, last, 1
        tick = t
        p = q
    return w, last, 0


def held_ons(b, base, tick, run, t_stop, sk, muted, need, off, tk, vl, pos):
    """(PLAN B1) b = a track's bytes from a checkpoint (as piece_track). Every note-on before t_stop that would be
    copied (not hidden / muted / a key past 127) on a place with need[k] > 0 goes into that place's ring: tick + velocity
    at tk / vl[off[k] + pos[k] % need[k]], pos[k] counting them all. So afterwards each place holds its NEWEST need[k]
    note-ons (= the copies still sounding there: notes pair first in, first out). Returns 1 when the bytes break."""
    n = len(b)
    p = 0
    si = 0
    nsk = len(sk)
    while p < n:
        pos_b = base + p
        while si < nsk and sk[si] < pos_b:
            si += 1
        hid = si < nsk and sk[si] == pos_b
        q = p
        d = 0
        while q < n:
            c = np.int64(b[q])
            q += 1
            d = (d << 7) | (c & 127)
            if c < 128:
                break
        t = tick + d
        if t >= t_stop:
            return 0
        if q >= n:
            return 0
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        elif run == 0:
            return 1
        else:
            s = run
        if s < 0xF0:
            hi = s >> 4
            need_b = 1 if hi == 0xC or hi == 0xD else 2
            if q + need_b > n:
                return 1
            run = s
            ch = s & 15
            if hi == 0x9 and not hid and (muted >> ch) & 1 == 0:
                key = np.int64(b[q])
                v = np.int64(b[q + 1])
                k = ch * 256 + key
                if key < 128 and v > 0 and need[k] > 0:
                    i = off[k] + pos[k] % need[k]
                    tk[i] = t
                    vl[i] = v
                    pos[k] += 1
            q += need_b
        elif s == 0xFF or s == 0xF0 or s == 0xF7:
            mt = -1
            if s == 0xFF:
                if q >= n:
                    return 1
                mt = np.int64(b[q])
                q += 1
            ln = 0
            while q < n:
                c = np.int64(b[q])
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            if q + ln > n:
                return 1
            if mt == 0x2F:
                return 0
            q += ln
        else:
            return 1
        tick = t
        p = q
    return 0


def note_ons(b, base, tick, run, t_stop, sk, muted, want, out_t, out_q, out_v):
    """(PLAN B3) b = a track's bytes from a checkpoint (as piece_track): every note-on before t_stop that would be
    copied (not hidden / muted / a key past 127) on a place with want[k] != 0 -> out_t / out_q / out_v (tick, place,
    velocity), in order. Returns how many (out holds len(b) // 3 + 1: an event takes 3 bytes at least)."""
    n = len(b)
    p = 0
    si = 0
    nsk = len(sk)
    got = 0
    while p < n:
        pos_b = base + p
        while si < nsk and sk[si] < pos_b:
            si += 1
        hid = si < nsk and sk[si] == pos_b
        q = p
        d = 0
        while q < n:
            c = np.int64(b[q])
            q += 1
            d = (d << 7) | (c & 127)
            if c < 128:
                break
        t = tick + d
        if t >= t_stop or q >= n:
            return got
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        elif run == 0:
            return got
        else:
            s = run
        if s < 0xF0:
            hi = s >> 4
            need_b = 1 if hi == 0xC or hi == 0xD else 2
            if q + need_b > n:
                return got
            run = s
            ch = s & 15
            if hi == 0x9 and not hid and (muted >> ch) & 1 == 0:
                key = np.int64(b[q])
                v = np.int64(b[q + 1])
                k = ch * 256 + key
                if key < 128 and v > 0 and want[k] != 0 and got < len(out_t):
                    out_t[got] = t
                    out_q[got] = k
                    out_v[got] = v
                    got += 1
            q += need_b
        elif s == 0xFF or s == 0xF0 or s == 0xF7:
            mt = -1
            if s == 0xFF:
                if q >= n:
                    return got
                mt = np.int64(b[q])
                q += 1
            ln = 0
            while q < n:
                c = np.int64(b[q])
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            if q + ln > n or mt == 0x2F:
                return got
            q += ln
        else:
            return got
        tick = t
        p = q
    return got


def held_head(tk, qs, vl, newch, last, out):
    """(PLAN B1) The held copies as note-on events, each where it began: tk (ticks, in order), qs (channel x 256 +
    key), vl (velocities); waits from `last`. Returns (bytes written, the last tick). out holds 13 bytes a copy."""
    w = 0
    for i in range(len(tk)):
        dd = tk[i] - last
        m = 0
        tmp = dd
        while True:
            m += 1
            tmp >>= 7
            if tmp == 0:
                break
        for j in range(m - 1, -1, -1):
            out[w] = ((dd >> (7 * j)) & 127) | (0x80 if j > 0 else 0)
            w += 1
        ch = newch if newch >= 0 else qs[i] >> 8
        out[w] = 0x90 | ch
        out[w + 1] = qs[i] & 255
        out[w + 2] = max(1, vl[i])
        w += 3
        last = tk[i]
    return w, last


def runs_sort_fold(key, sig, cnt, n, tk, ts, tc, bits):
    """(PLAN D2) Counted notes key / sig / cnt[:n] sorted by key (stable: same key keeps its order = the tracks'
    order, as a synth merges them), then each run of the same key + sig back to back folded into one (counts added).
    tk / ts / tc = scratch as long. bits = the keys' highest bit + 1. Returns how many are left (in key / sig / cnt)."""
    shift = 0
    in_main = True
    hist = np.zeros(16385, np.int64)
    while shift < bits:
        if in_main:
            kk, ss, cc, dk, ds, dc = key, sig, cnt, tk, ts, tc
        else:
            kk, ss, cc, dk, ds, dc = tk, ts, tc, key, sig, cnt
        hist[:] = 0
        for i in range(n):
            hist[((kk[i] >> shift) & 16383) + 1] += 1
        for d in range(16384):
            hist[d + 1] += hist[d]
        for i in range(n):
            d = (kk[i] >> shift) & 16383
            o = hist[d]
            hist[d] = o + 1
            dk[o] = kk[i]
            ds[o] = ss[i]
            dc[o] = cc[i]
        in_main = not in_main
        shift += 14
    if not in_main:
        for i in range(n):
            key[i] = tk[i]
            sig[i] = ts[i]
            cnt[i] = tc[i]
    m = 0
    for i in range(n):
        if m > 0 and key[m - 1] == key[i] and sig[m - 1] == sig[i]:
            cnt[m - 1] += cnt[i]
        else:
            key[m] = key[i]
            sig[m] = sig[i]
            cnt[m] = cnt[i]
            m += 1
    return m


def runs_merge(ak, asg, ac, na, bk, bsg, bc, nb, ok, osg, oc):
    """(PLAN D2) Two sorted, folded run lists (runs_sort_fold) into ok / osg / oc: by key, a key's runs from a
    (earlier tracks) before b's, folded where they meet. Returns how many."""
    i = 0
    j = 0
    m = 0
    while i < na or j < nb:
        if j >= nb or (i < na and ak[i] <= bk[j]):
            k = ak[i]
            s = asg[i]
            c = ac[i]
            i += 1
        else:
            k = bk[j]
            s = bsg[j]
            c = bc[j]
            j += 1
        if m > 0 and ok[m - 1] == k and osg[m - 1] == s:
            oc[m - 1] += c
        else:
            ok[m] = k
            osg[m] = s
            oc[m] = c
            m += 1
    return m


def order_ok(key, sig, cnt, n, bar_ch):
    """(PLAN D2) Sorting put a tick's note-offs before its note-ons on each place (key's low bit, piece_track): the
    same pairing as the tracks' order (first in, first out) when every off there ends a note begun BEFORE that tick,
    i.e. a tick's offs <= the notes sounding just before it. Places on channels with a pedal / all-notes-off
    (bar_ch bit 2) weren't moved. Returns False where that fails anywhere (the caller keeps the tracks' order)."""
    sounding = np.zeros(1 << 15, np.int64)
    for i in range(n):
        k = key[i]
        place = (k >> 1) & 32767
        if sig[i] > 0:
            sounding[place] += cnt[i]
        else:
            if bar_ch[place >> 7] & 2 == 0 and cnt[i] > sounding[place]:
                return False
            sounding[place] = max(0, sounding[place] - cnt[i])
    return True


def open_at(key, sig, cnt, n, lim, wlim, take):
    """(Rendered ahead, carried notes) The counted notes key / sig / cnt[:n] (sorted: the order the piece's synth
    pairs them in) before key lim (a tick << 16), paired as a synth pairs them (an off ends its place's OLDEST copy,
    first in first out): take[:n] = the copies of each note-on still sounding at lim or ended from wlim on, and every
    note-off from wlim on (whole). The same pairing again from just these: those ended from wlim on are the oldest
    ones present when their offs come. Returns the rows taken."""
    opn = np.zeros(1 << 15, np.int64)
    gone = np.zeros(1 << 15, np.int64)  # (copies ended before wlim: each place's oldest, left out)
    m = 0
    while m < n and key[m] < lim:
        m += 1
    for i in range(m):
        place = (key[i] >> 1) & 32767
        if sig[i] > 0:
            opn[place] += cnt[i]
        else:
            k = min(cnt[i], opn[place])
            opn[place] -= k
            if key[i] < wlim:
                gone[place] += k
    rows = 0
    for i in range(m):
        take[i] = 0
        place = (key[i] >> 1) & 32767
        if sig[i] > 0:
            k = min(gone[place], cnt[i])
            gone[place] -= k
            take[i] = cnt[i] - k
        elif key[i] >= wlim:
            take[i] = cnt[i]
        if take[i] > 0:
            rows += 1
    return rows


def counted_track(key, sig, cnt, n, port, rel, out):
    """(PLAN D2) The counted notes of one port (key / sig / cnt[:n], sorted: runs_sort_fold) as a track's events
    from tick 0 = the piece's origin (a note's tick = (key >> 16) + rel): a run of 5 or more = the repeat mark
    FF 7F 08 "SWn" key count, then the note once (our Merlin reads it as that many: kestrel-midi REPEAT_MARK);
    fewer = written out. out holds n * 32 + 16. Returns (bytes written, the last tick written)."""
    w = 0
    last = 0
    for i in range(n):
        k = key[i]
        if (k >> 12) & 15 != port:
            continue
        t = (k >> 16) + rel
        ch = (k >> 8) & 15
        note = (k >> 1) & 127
        v = sig[i]
        c = cnt[i]
        times = c
        dd = t - last
        if c >= 5:
            times = 1
            m = 0
            tmp = dd
            while True:
                m += 1
                tmp >>= 7
                if tmp == 0:
                    break
            for j in range(m - 1, -1, -1):
                out[w] = ((dd >> (7 * j)) & 127) | (0x80 if j > 0 else 0)
                w += 1
            out[w] = 0xFF
            out[w + 1] = 0x7F
            out[w + 2] = 8
            out[w + 3] = 83  # S
            out[w + 4] = 87  # W
            out[w + 5] = 110  # n
            out[w + 6] = note
            out[w + 7] = (c >> 24) & 255
            out[w + 8] = (c >> 16) & 255
            out[w + 9] = (c >> 8) & 255
            out[w + 10] = c & 255
            w += 11
            dd = 0
        for r in range(times):
            m = 0
            tmp = dd
            while True:
                m += 1
                tmp >>= 7
                if tmp == 0:
                    break
            for j in range(m - 1, -1, -1):
                out[w] = ((dd >> (7 * j)) & 127) | (0x80 if j > 0 else 0)
                w += 1
            out[w] = (0x90 if v > 0 else 0x80) | ch
            out[w + 1] = note
            out[w + 2] = v
            w += 3
            dd = 0
        last = t
    return w, last
