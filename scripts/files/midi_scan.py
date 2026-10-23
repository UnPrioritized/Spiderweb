"""The byte loops for reading and copying MIDI files (Load MIDI). Plain Python here; files/fastloops.py compiles the
very same functions with Numba (midi_walk / midi_rewrite), so both give identical results. Every loop works on a
piece of one track at a time and stops at the last whole event, so a huge track goes through a small buffer: call
again with the rest (st carries what it needs across pieces).

walk     reads events and counts them, checking each byte (a damaged file never reads past its end)
rewrite  writes a track again event by event, its ticks moved through a table: an unchanged gap keeps its own
         bytes, running status as in the file (an explicit status only where the one written last differs)"""

import numpy as np

# walk(): st = [tick, running status (0 = none), End of Track seen, flags, bytes after End of Track, last meta /
# sysex (1 = the event before was one)]
ST_TICK, ST_RUN, ST_EOT, ST_FLAGS, ST_AFTER, ST_META = range(6)
ST_SIZE = 6
# flags
BAD = 1  # unreadable from here on (running status before any status, a system message, a gap past 8 bytes)
CUT = 2  # the track ends in the middle of an event
NO_EOT = 4  # no End of Track (set by the reader once the track is done)
LONG_GAP = 8  # a gap (or length) written in more than 4 bytes
RUN_AFTER_META = 16  # running status right after a meta / sysex event (programs read these differently)
AFTER_EOT = 32  # bytes after End of Track
ODD_TEMPO = 64  # a tempo event that isn't 3 bytes long
HIGH_DATA = 128  # a data byte over 127 (keys 128-255 of 256 keys MIDIs)
# cnt: counts of each kind
C_EVENTS, C_ON, C_OFF, C_ON0, C_POLY, C_CC, C_PROG, C_PRESS, C_BEND, C_TEMPO, C_META, C_SYSEX, C_CHANNELS = range(13)
C_SIZE = 13


def walk(b, p, end, final, st, cnt):
    """Reads the whole events in b[p:end] (one track's bytes; final = the track ends at end). Returns where it
    stopped: at end, or at the start of an event not wholly in b yet (call again with more)."""
    tick = st[ST_TICK]
    run = st[ST_RUN]
    flags = st[ST_FLAGS]
    meta = st[ST_META]
    while True:
        if st[ST_EOT]:  # (whatever comes after End of Track is kept as it is, never read)
            if end > p:
                st[ST_AFTER] += end - p
                flags |= AFTER_EOT
            p = end
            break
        if p >= end:
            break
        q = p
        d = 0
        n = 0
        whole = True
        while True:
            if q >= end:
                whole = False
                break
            c = b[q]
            q += 1
            d = (d << 7) | (c & 127)
            n += 1
            if c < 128:
                break
            if n == 8:
                flags |= BAD
                break
        if flags & BAD:
            p = end
            break
        if whole and q >= end:
            whole = False
        if whole:
            if n > 4:
                flags |= LONG_GAP
            s = np.int64(b[q])
            explicit = s >= 128
            if explicit:
                q += 1
            elif run == 0:
                flags |= BAD
                p = end
                break
            else:
                s = run
                if meta:
                    flags |= RUN_AFTER_META
            if s < 0xF0:
                hi = s >> 4
                need = 1 if hi == 0xC or hi == 0xD else 2
                if q + need > end:
                    whole = False
                else:  # (a data byte over 127 = 256 keys black MIDIs' keys: read as data, like they do)
                    if b[q] >= 128 or (need == 2 and b[q + 1] >= 128):
                        flags |= HIGH_DATA
                    ch = s & 15
                    if hi == 0x9:
                        if b[q + 1] > 0:
                            cnt[C_ON] += 1
                        else:
                            cnt[C_ON0] += 1
                    elif hi == 0x8:
                        cnt[C_OFF] += 1
                    elif hi == 0xA:
                        cnt[C_POLY] += 1
                    elif hi == 0xB:
                        cnt[C_CC] += 1
                    elif hi == 0xC:
                        cnt[C_PROG] += 1
                    elif hi == 0xD:
                        cnt[C_PRESS] += 1
                    else:
                        cnt[C_BEND] += 1
                    cnt[C_CHANNELS] |= np.int64(1) << ch
                    if explicit:
                        run = s
                    q += need
                    meta = 0
            elif s == 0xFF or s == 0xF0 or s == 0xF7:
                mt = -1
                if s == 0xFF:
                    if q >= end:
                        whole = False
                    else:
                        mt = np.int64(b[q])
                        q += 1
                if whole:
                    ln = 0
                    m = 0
                    while True:
                        if q >= end:
                            whole = False
                            break
                        c = b[q]
                        q += 1
                        ln = (ln << 7) | (c & 127)
                        m += 1
                        if c < 128:
                            break
                        if m == 8:
                            flags |= BAD
                            break
                    if flags & BAD:
                        p = end
                        break
                    if whole and q + ln > end:
                        whole = False
                    if whole:
                        if m > 4:
                            flags |= LONG_GAP
                        if s != 0xFF:
                            cnt[C_SYSEX] += 1
                        elif mt == 0x51:
                            cnt[C_TEMPO] += 1
                            if ln != 3:
                                flags |= ODD_TEMPO
                        else:
                            cnt[C_META] += 1
                            if mt == 0x2F:
                                st[ST_EOT] = 1
                        q += ln
                        meta = 1
            else:  # (F1-FE but F7 / FF: not allowed in a file)
                flags |= BAD
                p = end
                break
        if not whole:
            if final:
                flags |= CUT
                p = end
            break
        tick += d
        cnt[C_EVENTS] += 1
        p = q
    st[ST_TICK] = tick
    st[ST_RUN] = run
    st[ST_FLAGS] = flags
    st[ST_META] = meta
    return p


# rewrite(): st = [tick read, running status read, tick written, running status written, End of Track seen]
RW_TICK, RW_RUN, RW_DONE, RW_WRUN, RW_EOT = range(5)
RW_SIZE = 5


def rewrite(b, p, end, final, table, use_table, out, o, st):
    """Writes the whole events in b[p:end] (a track walk() read without BAD) into out from o, each event's tick
    moved to table[tick] (use_table; past the table's end: moved like its last tick). Stops at the first event not
    wholly in b, or one that wouldn't fit in out. Returns (where it stopped in b, where it stopped in out)."""
    tick = st[RW_TICK]
    run = st[RW_RUN]
    done = st[RW_DONE]
    wrun = st[RW_WRUN]
    room = len(out)
    last = len(table) - 1
    while p < end:
        if st[RW_EOT]:  # (bytes after End of Track: copied as they are)
            k = min(end - p, room - o)
            out[o:o + k] = b[p:p + k]
            o += k
            p += k
            break
        q = p
        d = 0
        whole = True
        while True:
            if q >= end:
                whole = False
                break
            c = b[q]
            q += 1
            d = (d << 7) | (c & 127)
            if c < 128:
                break
        gap_end = q
        if whole and q >= end:
            whole = False
        eot = False
        explicit = False
        s = np.int64(0)
        if whole:
            s = np.int64(b[q])
            explicit = s >= 128
            if explicit:
                q += 1
            else:
                s = run
            if s < 0xF0:
                q += 1 if (s >> 4) == 0xC or (s >> 4) == 0xD else 2
                if q > end:
                    whole = False
            else:
                if s == 0xFF:
                    if q >= end:
                        whole = False
                    else:
                        eot = b[q] == 0x2F
                        q += 1
                ln = 0
                while whole:
                    if q >= end:
                        whole = False
                        break
                    c = b[q]
                    q += 1
                    ln = (ln << 7) | (c & 127)
                    if c < 128:
                        break
                q += ln
                if q > end:
                    whole = False
        if not whole:
            break
        t = tick + d
        if use_table:
            nt = table[t] if t <= last else table[last] + (t - last)
        else:
            nt = t
        dd = nt - done
        if dd < 0:
            dd = 0
            nt = done
        add = 1 if (not explicit and s < 0xF0 and wrun != s) else 0
        size = (gap_end - p if dd == d else 10) + add + (q - gap_end)
        if o + size > room:
            break
        if dd == d:  # (an unchanged gap keeps its own bytes: odd long ones too)
            for k in range(p, gap_end):
                out[o] = b[k]
                o += 1
        else:
            nb = 1
            x = dd >> 7
            while x > 0:
                nb += 1
                x >>= 7
            for k in range(nb - 1, -1, -1):
                v = (dd >> (7 * k)) & 127
                if k > 0:
                    v |= 128
                out[o] = v
                o += 1
        if add:
            out[o] = s
            o += 1
        for k in range(gap_end, q):
            out[o] = b[k]
            o += 1
        if s < 0xF0:
            if explicit:
                run = s
            wrun = s
        tick = t
        done = nt
        p = q
        if eot:
            st[RW_EOT] = 1
    st[RW_TICK] = tick
    st[RW_RUN] = run
    st[RW_DONE] = done
    st[RW_WRUN] = wrun
    return p, o
