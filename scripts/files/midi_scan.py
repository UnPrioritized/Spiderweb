"""The byte loops for reading and copying MIDI files (Load MIDI). Plain Python here; files/fastloops.py compiles the
very same functions with Numba (midi_walk / midi_rewrite), so both give identical results. Every loop works on a
piece of one track at a time and stops at the last whole event, so a huge track goes through a small buffer: call
again with the rest (st carries what it needs across pieces).

walk     reads events and counts them, checking each byte (a damaged file never reads past its end)
rewrite  writes a track again event by event (ticks moved by a map, tempo events left out / put in): an
         unchanged gap keeps its own bytes, running status as in the file (an explicit status only where the one
         written last differs)
note_ticks  marks the ticks with note-ons / offs and other channel events (Keep timing's anchors)
busy_hits   marks Spiderweb's own notes sounding with a track's notes on the same channel + key (the export's count)
gather      a track's notes starting in a box (Extract): each one's ticks, key, velocity and where its note-on /
            note-off events are in the track (what readers skip once it's extracted)

HIDDEN NOTES (Extract): a sorted array of the track's byte offsets (skip) of the events left out, the note-on and
the note-off a note was paired with (first on, first off, counted with every note: the pairing is the file's).
Readers skip an event whose first byte (its gap) is at one; st keeps where in skip they are."""

import numpy as np

# walk(): st = [tick, running status (0 = none), End of Track seen, flags, bytes after End of Track, last meta /
# sysex (1 = the event before was one), where the last call's good events end (BAD / CUT: the bytes from there on
# aren't events)]
ST_TICK, ST_RUN, ST_EOT, ST_FLAGS, ST_AFTER, ST_META, ST_GOOD = range(7)
ST_SIZE = 7
# flags
BAD = 1  # unreadable from here on (running status before any status, a system message, a gap past 8 bytes,
#   ticks past MOST_TICK)
CUT = 2  # the track ends in the middle of an event
NO_EOT = 4  # no End of Track (set by the reader once the track is done)
LONG_GAP = 8  # a gap (or length) written in more than 4 bytes
RUN_AFTER_META = 16  # running status right after a meta / sysex event (programs read these differently)
AFTER_EOT = 32  # bytes after End of Track
ODD_TEMPO = 64  # a tempo event that isn't 3 bytes long
HIGH_DATA = 128  # a data byte over 127 (keys 128-255 of 256 keys MIDIs)
MOST_TICK = 1 << 48  # a track reaching a later tick is damaged (BAD)
# cnt: counts of each kind
C_EVENTS, C_ON, C_OFF, C_ON0, C_POLY, C_CC, C_PROG, C_PRESS, C_BEND, C_TEMPO, C_META, C_SYSEX, C_CHANNELS = range(13)
C_SIZE = 13


def walk(b, p, end, final, rest, st, cnt):
    """Reads the whole events in b[p:end] (one track's bytes; final = the track ends at end; rest = how many more
    bytes the track has after end). Returns where it stopped: at end, or at the start of an event not wholly in b
    yet (call again with more). An event that can't end inside the track = CUT at once (never waited for)."""
    tick = st[ST_TICK]
    run = st[ST_RUN]
    flags = st[ST_FLAGS]
    meta = st[ST_META]
    good = p
    while True:
        good = p
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
        beyond = False  # (an event running past the track's end)
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
                    beyond = q + need > end + rest
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
                        beyond = q + ln > end + rest
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
            if final or beyond:
                flags |= CUT
                p = end
            break
        tick += d
        if tick > MOST_TICK:  # (a damaged file's gaps adding up past any song)
            flags |= BAD
            p = end
            break
        cnt[C_EVENTS] += 1
        p = q
    st[ST_TICK] = tick
    st[ST_RUN] = run
    st[ST_FLAGS] = flags
    st[ST_META] = meta
    st[ST_GOOD] = good if flags & (BAD | CUT) else p
    return p


# rewrite(): st = [tick read, running status read, tick written, running status written, End of Track seen, next
# event to put in (ins_t)]
RW_TICK, RW_RUN, RW_DONE, RW_WRUN, RW_EOT, RW_INS, RW_SKIP = range(7)
RW_SIZE = 7


def rewrite(b, p, end, final, bp_n, bp_f, use_map, drop, ins_t, ins_at, ins_b, out, o, st, base, skip):
    """Writes the whole events in b[p:end] (a track walk() read without BAD) into out from o. use_map: each event's
    tick moved through the Keep timing map (bp_n -> bp_f: tempo_file.file_plan's to_file, the very same rule:
    anchors exactly, between two the same share of the way). drop[type] = 1: meta events of that type left
    out (a tempo list written anew), their time carried on. ins_t / ins_at / ins_b: events put in (ticks written,
    in order; event k = ins_b[ins_at[k]:ins_at[k + 1]]), each before the first event at or after its tick; the
    ones past the track's end before its End of Track (which then moves later). Hidden events (Extract: skip, b[0]
    = the track's byte base) left out, their time carried on. Stops at the first event not wholly in b, or one
    that wouldn't fit in out. Returns (where it stopped in b, where it stopped in out)."""
    tick = st[RW_TICK]
    run = st[RW_RUN]
    done = st[RW_DONE]
    wrun = st[RW_WRUN]
    k = st[RW_INS]
    si = st[RW_SKIP]
    n_ins = len(ins_t)
    room = len(out)
    while p < end:
        if st[RW_EOT]:  # (bytes after End of Track: copied as they are)
            c = min(end - p, room - o)
            out[o:o + c] = b[p:p + c]
            o += c
            p += c
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
        mt = -1
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
                        mt = np.int64(b[q])
                        eot = mt == 0x2F
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
        nt = t
        if use_map:  # (the map: written out here, the compiled loop can't call a helper)
            lo = 0
            hi = len(bp_n)
            while lo < hi:
                mid = (lo + hi) // 2
                if bp_n[mid] <= t:
                    lo = mid + 1
                else:
                    hi = mid
            i = max(lo - 1, 0)
            nt = bp_f[i] + (t - bp_n[i])
            j = min(i + 1, len(bp_n) - 1)
            gd = bp_n[j] - bp_n[i]
            fd = bp_f[j] - bp_f[i]
            if j > i and t > bp_n[i] and gd > 0 and fd != gd:
                share = bp_f[i] + np.int64(np.rint((t - bp_n[i]) * fd / gd))
                if share == bp_f[i]:
                    share = bp_f[i] + min(fd, 1)
                nt = share
        # events put in before this one (all that are left before End of Track)
        full = False
        while k < n_ins and (ins_t[k] <= nt or eot):
            size = ins_at[k + 1] - ins_at[k]
            if o + 10 + size > room:
                full = True
                break
            gap = max(ins_t[k] - done, 0)
            nb = 1
            x = gap >> 7
            while x > 0:
                nb += 1
                x >>= 7
            for m in range(nb - 1, -1, -1):
                v = (gap >> (7 * m)) & 127
                if m > 0:
                    v |= 128
                out[o] = v
                o += 1
            for m in range(ins_at[k], ins_at[k + 1]):
                out[o] = ins_b[m]
                o += 1
            done += gap
            k += 1
            wrun = -1  # (a meta event ends running status: the next channel event writes its status, hunt 2026-10-24)
        if full:
            break
        if mt >= 0 and mt < len(drop) and drop[mt]:  # (left out: its time goes on to the next event)
            tick = t
            p = q
            continue
        while si < len(skip) and skip[si] < base + p:
            si += 1
        if si < len(skip) and skip[si] == base + p and s < 0xF0:  # (an extracted note's on / off: left out too)
            if explicit:
                run = s
            tick = t
            p = q
            continue
        dd = nt - done
        if dd < 0:
            dd = 0
            nt = done
        add = 1 if (not explicit and s < 0xF0 and wrun != s) else 0
        size = (gap_end - p if dd == d else 10) + add + (q - gap_end)
        if o + size > room:
            break
        if dd == d:  # (an unchanged gap keeps its own bytes: odd long ones too)
            for m in range(p, gap_end):
                out[o] = b[m]
                o += 1
        else:
            nb = 1
            x = dd >> 7
            while x > 0:
                nb += 1
                x >>= 7
            for m in range(nb - 1, -1, -1):
                v = (dd >> (7 * m)) & 127
                if m > 0:
                    v |= 128
                out[o] = v
                o += 1
        if add:
            out[o] = s
            o += 1
        for m in range(gap_end, q):
            out[o] = b[m]
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
    st[RW_INS] = k
    st[RW_SKIP] = si
    return p, o


# note_ticks(): st = [tick, running status]
NT_TICK, NT_RUN = range(2)
NT_SIZE = 2
NOTE_TICK, OTHER_TICK = 1, 2  # mark[tick]: a note-on / off there; another channel event (CC, bend...) there


def note_ticks(b, p, end, final, mark, base, st):
    """Marks the ticks of the whole events in b[p:end] (a track walk() read without BAD): mark[tick - base] |=
    NOTE_TICK for a note-on / off, OTHER_TICK for any other channel event (ticks outside base .. base + len(mark):
    not marked; a long song is read a window of ticks at a time). Keep timing's anchors on a loaded MIDI. Returns
    where it stopped (an event not wholly in b: call again with more)."""
    tick = st[NT_TICK]
    run = st[NT_RUN]
    top = base + len(mark)
    while p < end:
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
        if whole and q >= end:
            whole = False
        if not whole:
            break
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        else:
            s = run
        if s < 0xF0:
            q += 1 if (s >> 4) == 0xC or (s >> 4) == 0xD else 2
            if q > end:
                break
            if s >= 128:
                run = s
            t = tick + d
            if base <= t < top:
                mark[t - base] |= NOTE_TICK if (s >> 4) == 0x8 or (s >> 4) == 0x9 else OTHER_TICK
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
                c = b[q]
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            q += ln
            if not whole or q > end:
                break
        tick += d
        p = q
    st[NT_TICK] = tick
    st[NT_RUN] = run
    return p


# busy_hits(): st = [tick, running status, stopped at t_stop, place in skip]
BH_TICK, BH_RUN, BH_STOP, BH_SKIP = range(4)
BH_SIZE = 4


def busy_hits(b, p, end, base, skip, t_stop, st, cnt, since, lane_at, own_s, own_e, own_top, hit):
    """Spiderweb's own notes sounding at the same time as this track's notes on the same channel + key: hit[i] = 1.
    Own notes by lane (channel x 256 + key): lane_at[q] .. lane_at[q + 1], sorted by start (own_s), own_e their
    ends, own_top the latest end so far in the lane. cnt[q] = the track's notes sounding on lane q, since[q] = from
    when (a track walk() read without BAD, from a checkpoint: its open counts). b[0] = the track's byte base;
    skip = its hidden events (Extract). Stops at the first event at or after t_stop (st[BH_STOP] = 1) or one not
    wholly in b; returns where. Lanes still sounding: the caller ends them."""
    tick = st[BH_TICK]
    run = st[BH_RUN]
    si = st[BH_SKIP]
    while p < end:
        while si < len(skip) and skip[si] < base + p:
            si += 1
        hid = si < len(skip) and skip[si] == base + p
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
        if whole and q >= end:
            whole = False
        if not whole:
            break
        t = tick + d
        if t >= t_stop:
            st[BH_STOP] = 1
            break
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        else:
            s = run
        if s < 0xF0:
            hi = s >> 4
            need = 1 if hi == 0xC or hi == 0xD else 2
            if q + need > end:
                break
            if s >= 128:
                run = s
            if (hi == 0x8 or hi == 0x9) and not hid:
                ln = (s & 15) * 256 + np.int64(b[q])
                if hi == 0x9 and b[q + 1] > 0:
                    if cnt[ln] == 0:
                        since[ln] = t
                    cnt[ln] += 1
                elif cnt[ln] > 0:
                    cnt[ln] -= 1
                    if cnt[ln] == 0 and t > since[ln]:  # (a stretch of time this lane sounded: own notes in it)
                        a = since[ln]
                        lo = lane_at[ln]
                        top = lane_at[ln + 1]
                        while lo < top:  # (own notes starting before t)
                            mid = (lo + top) // 2
                            if own_s[mid] < t:
                                lo = mid + 1
                            else:
                                top = mid
                        j = lo - 1
                        while j >= lane_at[ln] and own_top[j] > a:
                            if own_e[j] > a:
                                hit[j] = 1
                            j -= 1
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
                c = b[q]
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            q += ln
            if not whole or q > end:
                break
        tick = t
        p = q
    st[BH_TICK] = tick
    st[BH_RUN] = run
    st[BH_SKIP] = si
    return p


# gather(): st = [tick, running status, notes found, of them still sounding, place in skip, done]
GA_TICK, GA_RUN, GA_N, GA_OPEN, GA_SKIP, GA_DONE = range(6)
GA_SIZE = 6


def gather(b, p, end, base, skip, t0, t1, k0, k1, chans, st, old, newer, head, tail, nxt, o_s, o_e, o_k, o_v, o_on,
           o_off, o_was):
    """A track's notes starting in ticks t0 .. t1 - 1 on keys k0..k1 of the channels chans[ch] = 1 (Extract), read
    from a checkpoint (old = notes sounding per channel + key there: all older). Each found note i: o_s / o_e its
    start / end tick (end -1 = not let go yet), o_k channel x 256 + key, o_v velocity, o_on / o_off its note-on /
    note-off's byte place in the track (b[0] = byte base; o_off -1 until let go), o_was = 1 when it's hidden already
    (skip). Paired first on, first off with EVERY note (newer = ones starting at t1 or later, never ending one of
    the box's first). Done (st[GA_DONE] = 1) once past t1 with none of them sounding. Stops when o_* are full (st
    [GA_N] = their length: call again with bigger ones) or at an event not wholly in b; returns where."""
    tick = st[GA_TICK]
    run = st[GA_RUN]
    n = st[GA_N]
    si = st[GA_SKIP]
    room = len(o_s)
    while p < end and st[GA_DONE] == 0 and n < room:
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
        if whole and q >= end:
            whole = False
        if not whole:
            break
        t = tick + d
        if t >= t1 and st[GA_OPEN] == 0:
            st[GA_DONE] = 1
            break
        while si < len(skip) and skip[si] < base + p:
            si += 1
        hid = si < len(skip) and skip[si] == base + p
        s = np.int64(b[q])
        if s >= 128:
            q += 1
        else:
            s = run
        if s < 0xF0:
            hi = s >> 4
            need = 1 if hi == 0xC or hi == 0xD else 2
            if q + need > end:
                break
            if s >= 128:
                run = s
            if hi == 0x8 or hi == 0x9:
                k = np.int64(b[q])
                ch = s & 15
                ln = ch * 256 + k
                if chans[ch] and k0 <= k <= k1:
                    if hi == 0x9 and b[q + 1] > 0:
                        if t < t0:
                            old[ln] += 1
                        elif t < t1:
                            o_s[n] = t
                            o_e[n] = -1
                            o_k[n] = ln
                            o_v[n] = np.int64(b[q + 1])
                            o_on[n] = base + p
                            o_off[n] = -1
                            o_was[n] = 1 if hid else 0
                            nxt[n] = -1
                            if tail[ln] >= 0:
                                nxt[tail[ln]] = n
                            else:
                                head[ln] = n
                            tail[ln] = n
                            n += 1
                            st[GA_OPEN] += 1
                        else:
                            newer[ln] += 1
                    elif old[ln] > 0:
                        old[ln] -= 1
                    elif head[ln] >= 0:
                        i = head[ln]
                        o_e[i] = t
                        o_off[i] = base + p
                        head[ln] = nxt[i]
                        if head[ln] < 0:
                            tail[ln] = -1
                        st[GA_OPEN] -= 1
                    elif newer[ln] > 0:
                        newer[ln] -= 1
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
                c = b[q]
                q += 1
                ln = (ln << 7) | (c & 127)
                if c < 128:
                    break
            q += ln
            if not whole or q > end:
                break
        tick = t
        p = q
    st[GA_TICK] = tick
    st[GA_RUN] = run
    st[GA_N] = n
    st[GA_SKIP] = si
    return p
