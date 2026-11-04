"""Rendered ahead's pieces of a LOADED MIDI (PLAN.md L0 / L1): a track's piece is its own bytes, COPIED from a
checkpoint, never read into notes and written again (measured 2026-11-03: ~5x quicker; Shinki 4.5 s of music made
in 2 s instead of 9.3). Plain Python that files/fastloops.py compiles (piece_track; the same function both ways).

piece_track walks a track's bytes from a checkpoint. Before t_pre it only counts: which notes sound there (cnt,
channel x 256 + key, and the newest one's velocity / tick). From the first event at / after t_pre up to the first
past t_end it copies the events into out, changing only what playing would change:
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


def piece_track(b, base, tick, run, t_pre, t_end, sk, cnt, vel, ontk, muted, newch, last, out):
    """b = a track's bytes from a checkpoint at byte place base in the track (tick, running status there). Returns
    (bytes written to out, the tick of the last event written (last when none), bad = 1 when the track's bytes end
    or break before t_end). out must hold len(b) + 4 x hidden events + 16 bytes."""
    n = len(b)
    p = 0
    w = 0
    si = 0
    nsk = len(sk)
    copying = False
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
            if copying and not hid:
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
