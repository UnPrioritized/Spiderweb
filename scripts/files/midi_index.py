"""Load MIDI's pass over each track, in the same read that checks it (midi_read.MidiFile.scan's each=). Plain
Python here; files/fastloops.py compiles the very same functions (midi_index, midi_close, midi_grid), so both
give identical results. No note is kept: a track is read again from its bytes whenever its notes are needed, so
the pass leaves an index to start such a read anywhere, and the overview picture.

Notes: a note-on pairs with the first note-off on its channel + key that comes after it, first on, first off (the
user's check in Domino 2026-10-23: tick 0 vel 100 + tick 240 vel 50, offs at 480 + 960 = 480 long + 720 long). A
note-on with velocity 0 is a note-off. A note never let go ends where its track ends.

Index (a checkpoint every CP_LEAST bytes or more): where it is in the track, its tick and running status, and for
every channel + key with notes sounding there: how many, and where the NEWEST of them ends. First on, first off
means the newest ends last, so a read starting at the checkpoint knows that key sounds until then without a list
of the notes (Shinki holds 800 million at once).

Overview: per key x bucket (G ticks): how many notes sound there (a diff per key while reading, added up per track
at its end), and the highest track sounding there (drawn on top)."""

import numpy as np

# ix: one track's state, kept across its pieces
(X_TICK, X_RUN, X_STOP, X_USED, X_FREE, X_NOPEN, X_NOTES, X_ZERO, X_ORPHAN, X_HANG, X_LASTCP, X_NCP, X_NENT,
 X_KLO, X_KHI, X_FIRST, X_END, X_NMETA, X_GLO, X_GHI, X_EOT, X_LONG) = range(22)
X_SIZE = 22
# what index() needs when it stops early (Python makes it room and calls it again from where it stopped)
GO, MORE_POOL, MORE_CP, MORE_ENT, MORE_META, MORE_GRID, MORE_KEYS = range(7)
CP_LEAST = 64 << 10  # bytes between checkpoints at least
CP_PER_KEY = 4 << 10  # ... and this many more for each channel + key sounding there (their entries' cost)
META_KEEP = 64  # bytes kept of each kept meta event's data
QS = 16 * 256  # channel x key (keys up to 255: 256 keys MIDIs)
GRID_MOST = 1 << 18  # the overview's buckets at most (16,384 beats; notes past it are left out of it, drawn exactly)
FAR = 1 << 62


def new_state():
    ix = np.zeros(X_SIZE, np.int64)
    ix[X_FREE] = -1
    ix[X_LASTCP] = -FAR
    ix[X_KLO] = FAR
    ix[X_KHI] = -1
    ix[X_FIRST] = FAR
    ix[X_GLO] = FAR
    ix[X_GHI] = -1
    return ix


def index(b, p, end, at, G, ix, q_hd, q_tl, q_rl, q_pos, q_vel, opened, pt, pn, pw, cp_off, cp_tick, cp_run, cp_ent,
          e_q, e_cnt, e_end, e_vel, e_nx, cov, chn, m_tick, m_type, m_len, m_data):
    """Reads the whole events in b[p:end] (bytes midi_scan.walk found good; b[0] = the track's byte at) into the
    index. Returns (GO or what it needs more room for, where it stopped). q_vel / e_vel = the velocity of the note
    that began a key's sounding (cover's colour for it, the same wherever a read starts)."""
    tick = ix[X_TICK]
    run = ix[X_RUN]
    nk = cov.shape[0]
    ng = cov.shape[1]
    status = GO
    while p < end:
        if ix[X_STOP] or ix[X_EOT]:
            p = end
            break
        ev = p
        pos = at + ev
        nopen = ix[X_NOPEN]
        if pos - ix[X_LASTCP] >= max(CP_LEAST, CP_PER_KEY * nopen):
            c = ix[X_NCP]
            n = ix[X_NENT]
            if c + 1 >= len(cp_off):
                status = MORE_CP
                break
            if n + nopen > len(e_q):
                status = MORE_ENT
                break
            cp_off[c] = pos
            cp_tick[c] = tick
            cp_run[c] = run
            cp_ent[c] = n
            for j in range(nopen):
                q = opened[j]
                node = q_tl[q]
                e_q[n] = q
                e_cnt[n] = q_rl[q]
                e_end[n] = -1
                e_vel[n] = q_vel[q]
                e_nx[n] = pw[node]
                pw[node] = n
                n += 1
            cp_ent[c + 1] = n
            ix[X_NCP] = c + 1
            ix[X_NENT] = n
            ix[X_LASTCP] = pos
        q = p
        d = 0
        while q < end:
            c8 = np.int64(b[q])
            q += 1
            d = (d << 7) | (c8 & 127)
            if c8 < 128:
                break
        if q >= end:
            ix[X_STOP] = 1
            break
        t = tick + d
        s = np.int64(b[q])
        explicit = s >= 128
        if explicit:
            q += 1
        elif run == 0:
            ix[X_STOP] = 1
            break
        else:
            s = run
        if s < 0xF0:
            hi = s >> 4
            need = 1 if hi == 0xC or hi == 0xD else 2
            if q + need > end:
                ix[X_STOP] = 1
                break
            if hi == 0x9 or hi == 0x8:
                k = np.int64(b[q])
                v = np.int64(b[q + 1])
                ch = s & 15
                qq = ch * 256 + k
                if k >= nk:
                    status = MORE_KEYS
                    break
                if hi == 0x9 and v > 0:
                    g = t // G
                    far = g >= GRID_MOST - 1  # (past the overview's end: left out of it)
                    if not far and g >= ng:
                        status = MORE_GRID
                        break
                    node = ix[X_FREE]
                    if node >= 0:
                        ix[X_FREE] = pn[node]
                    else:
                        node = ix[X_USED]
                        if node >= len(pt):
                            status = MORE_POOL
                            break
                        ix[X_USED] = node + 1
                    pt[node] = t
                    pn[node] = -1
                    pw[node] = -1
                    if q_tl[qq] >= 0:
                        pn[q_tl[qq]] = node
                    else:
                        q_hd[qq] = node
                    q_tl[qq] = node
                    q_rl[qq] += 1
                    if q_rl[qq] == 1:
                        q_vel[qq] = v
                        q_pos[qq] = nopen
                        opened[nopen] = qq
                        ix[X_NOPEN] = nopen + 1
                    if not far:
                        cov[k, g] += 1
                        if g < ix[X_GLO]:
                            ix[X_GLO] = g
                    ix[X_NOTES] += 1
                    chn[ch] += 1
                    if k < ix[X_KLO]:
                        ix[X_KLO] = k
                    if k > ix[X_KHI]:
                        ix[X_KHI] = k
                    if t < ix[X_FIRST]:
                        ix[X_FIRST] = t
                elif q_rl[qq] == 0:
                    ix[X_ORPHAN] += 1
                else:
                    node = q_hd[qq]
                    s0 = pt[node]
                    ee = t if t > s0 else s0 + 1
                    g1 = min((ee - 1) // G + 1, GRID_MOST - 1)
                    far = s0 // G >= GRID_MOST - 1
                    if not far and g1 >= ng:
                        status = MORE_GRID
                        break
                    if t == s0:
                        ix[X_ZERO] += 1
                    if t - s0 > 65535:
                        ix[X_LONG] += 1
                    w = pw[node]
                    while w >= 0:
                        e_end[w] = t
                        w = e_nx[w]
                    if not far:
                        cov[k, g1] -= 1
                        if g1 > ix[X_GHI]:
                            ix[X_GHI] = g1
                    if ee > ix[X_END]:
                        ix[X_END] = ee
                    q_hd[qq] = pn[node]
                    if q_hd[qq] < 0:
                        q_tl[qq] = -1
                    pn[node] = ix[X_FREE]
                    ix[X_FREE] = node
                    q_rl[qq] -= 1
                    if q_rl[qq] == 0:
                        j = q_pos[qq]
                        last = opened[nopen - 1]
                        opened[j] = last
                        q_pos[last] = j
                        q_pos[qq] = -1
                        ix[X_NOPEN] = nopen - 1
            if explicit:
                run = s
            q += need
        elif s == 0xFF or s == 0xF0 or s == 0xF7:
            mt = -1
            if s == 0xFF:
                if q >= end:
                    ix[X_STOP] = 1
                    break
                mt = np.int64(b[q])
                q += 1
            ln = 0
            while q < end:
                c8 = np.int64(b[q])
                q += 1
                ln = (ln << 7) | (c8 & 127)
                if c8 < 128:
                    break
            if q + ln > end:
                ix[X_STOP] = 1
                break
            if mt == 0x03 or mt == 0x21 or mt == 0x51 or mt == 0x58 or mt == 0x59:
                m = ix[X_NMETA]
                if m >= len(m_tick):
                    status = MORE_META
                    break
                m_tick[m] = t
                m_type[m] = mt
                m_len[m] = ln
                for j in range(min(ln, META_KEEP)):
                    m_data[m, j] = b[q + j]
                ix[X_NMETA] = m + 1
            q += ln
            if mt == 0x2F:
                ix[X_EOT] = 1
        else:
            ix[X_STOP] = 1
            break
        tick = t
        p = q
    ix[X_TICK] = tick
    ix[X_RUN] = run
    return status, p


def close(ix, G, q_hd, q_rl, opened, pt, pn, pw, e_end, e_nx, cov):
    """The track's end: notes never let go end at its last tick (cov must reach that bucket)."""
    t = ix[X_TICK]
    for j in range(ix[X_NOPEN]):
        qq = opened[j]
        k = qq & 255
        node = q_hd[qq]
        while node >= 0:
            s0 = pt[node]
            ee = t if t > s0 else s0 + 1
            g1 = min((ee - 1) // G + 1, GRID_MOST - 1)
            w = pw[node]
            while w >= 0:
                e_end[w] = t
                w = e_nx[w]
            if s0 // G < GRID_MOST - 1:
                cov[k, g1] -= 1
                if g1 > ix[X_GHI]:
                    ix[X_GHI] = g1
            if ee > ix[X_END]:
                ix[X_END] = ee
            if t == s0:
                ix[X_ZERO] += 1
            ix[X_HANG] += 1
            node = pn[node]
        q_rl[qq] = 0
    ix[X_NOPEN] = 0


def grid(cov, glo, ghi, klo, khi, cnt, top, track, pic, pf):
    """A finished track's notes into the overview: sounding counts added to cnt, track to top where it's the
    highest, its own small picture (keys klo..khi x buckets glo.. in steps of pf) marked; cov cleared."""
    most = 2147483647
    for k in range(klo, khi + 1):
        c = 0
        for g in range(glo, ghi + 1):
            c += cov[k, g]
            cov[k, g] = 0
            if c > 0:
                was = cnt[k, g]
                cnt[k, g] = most if c > most - was else was + c
                if track > top[k, g]:
                    top[k, g] = track
                pic[k - klo, (g - glo) // pf] = 1


# cover(): st = [tick, running status, stopped (1 = past t1, 2 = can't be read on), spans waiting, place in skip]
C_TICK, C_RUN, C_STOP, C_SPANS, C_SKIP = range(5)
C_SIZE = 5


def cover(b, p, end, base, skip, st, cnt_q, first_q, vel_q, t1, close_at, ax, bx, W, k_lo, colour, tab, nx, filled,
          marks, sq, sa, sb, sv):
    """One track's notes painted onto a key x pixel table, reading its events in b[p:end] (from a checkpoint:
    cnt_q = notes sounding per channel + key there, first_q = since when, vel_q = the velocity of the one that
    began it). A key sounds while any note on it does (first on, first off doesn't change that), so a key's spans
    are painted, not its notes: tab[key - k_lo, x] = colour[channel, velocity // 4] where no track above painted
    yet (tracks go from the top one down; nx = each row's next free pixel, so every pixel is painted once).
    marks: 1 = a note starts at that pixel, 2 = a span ends there, 4 = a hidden note starts / ends there (under a
    track above, or one of a stack ending while the key sounds on). Pixel x of tick t = round(t * ax + bx).
    Reads up to tick t1; close_at >= 0 = the end: what still sounds is painted up to that tick. b[0] = the track's
    byte base; skip = its hidden events (Extract, midi_scan). Returns where it stopped in b."""
    tick = st[C_TICK]
    run = st[C_RUN]
    si = st[C_SKIP]
    nk = tab.shape[0]
    room = len(sq)
    n = st[C_SPANS]
    while True:
        while p < end and st[C_STOP] == 0 and n < room:
            while si < len(skip) and skip[si] < base + p:
                si += 1
            hid = si < len(skip) and skip[si] == base + p
            q = p
            d = 0
            while q < end:
                c8 = np.int64(b[q])
                q += 1
                d = (d << 7) | (c8 & 127)
                if c8 < 128:
                    break
            if q >= end:
                st[C_STOP] = 2
                break
            t = tick + d
            if t > t1:
                st[C_STOP] = 1
                break
            s = np.int64(b[q])
            explicit = s >= 128
            if explicit:
                q += 1
            elif run == 0:
                st[C_STOP] = 2
                break
            else:
                s = run
            if s < 0xF0:
                hi = s >> 4
                need = 1 if hi == 0xC or hi == 0xD else 2
                if q + need > end:
                    st[C_STOP] = 2
                    break
                if explicit:
                    run = s
                if (hi == 0x9 or hi == 0x8) and not hid:
                    k = np.int64(b[q])
                    v = np.int64(b[q + 1])
                    qq = (s & 15) * 256 + k
                    if hi == 0x9 and v > 0:
                        if cnt_q[qq] == 0:
                            first_q[qq] = t
                            vel_q[qq] = v
                        cnt_q[qq] += 1
                        kk = k - k_lo
                        x = np.int64(np.floor(t * ax + bx + 0.5))
                        if 0 <= kk < nk and 0 <= x < W:
                            marks[kk, x] |= 1 if tab[kk, x] < 0 else 4
                    elif cnt_q[qq] > 0:
                        cnt_q[qq] -= 1
                        if cnt_q[qq] == 0:
                            sq[n] = qq
                            sa[n] = first_q[qq]
                            sb[n] = t
                            sv[n] = vel_q[qq]
                            n += 1
                        else:  # (one of a stack ends, the key sounds on: hidden)
                            kk = k - k_lo
                            x = np.int64(np.floor(t * ax + bx + 0.5))
                            if 0 <= kk < nk and 0 <= x < W:
                                marks[kk, x] |= 4
                q += need
            elif s == 0xFF or s == 0xF0 or s == 0xF7:
                mt = -1
                if s == 0xFF:
                    if q >= end:
                        st[C_STOP] = 2
                        break
                    mt = np.int64(b[q])
                    q += 1
                ln = 0
                while q < end:
                    c8 = np.int64(b[q])
                    q += 1
                    ln = (ln << 7) | (c8 & 127)
                    if c8 < 128:
                        break
                if q + ln > end:
                    st[C_STOP] = 2
                    break
                q += ln
                if mt == 0x2F:
                    st[C_STOP] = 2
            else:
                st[C_STOP] = 2
                break
            tick = t
            p = q
        full = n >= room
        if not full and close_at >= 0 and (p >= end or st[C_STOP] != 0):  # (the end: what still sounds, up to it)
            for qq in range(len(cnt_q)):
                if cnt_q[qq] > 0:
                    if n >= room:
                        full = True
                        break
                    sq[n] = qq
                    sa[n] = first_q[qq]
                    sb[n] = close_at
                    sv[n] = vel_q[qq]
                    n += 1
                    cnt_q[qq] = 0
        for i in range(n):
            qq = sq[i]
            kk = (qq & 255) - k_lo
            if kk < 0 or kk >= nk:
                continue
            xa = np.int64(np.floor(sa[i] * ax + bx + 0.5))
            xb = np.int64(np.floor(sb[i] * ax + bx + 0.5))
            if xb <= xa:
                xb = xa + 1
            if xb <= 0 or xa >= W:
                continue
            ends = sb[i] <= t1 and xb <= W  # (its real end, in the table: not closed past the read stretch)
            if xa < 0:
                xa = 0
            if xb > W:
                xb = W
            code = colour[qq >> 8, min(sv[i], 127) // 4]
            x = xa
            last = -1
            while True:
                r = x
                while nx[kk, r] != r:
                    r = nx[kk, r]
                while nx[kk, x] != r:
                    y = nx[kk, x]
                    nx[kk, x] = r
                    x = y
                x = r
                if x >= xb:
                    break
                tab[kk, x] = code
                nx[kk, x] = x + 1
                filled[kk] += 1
                last = x
                x += 1
            if ends and last == xb - 1:
                marks[kk, last] |= 2
            elif ends:  # (its end under a track above)
                marks[kk, xb - 1] |= 4
        n = 0
        if not full:
            break
    st[C_TICK] = tick
    st[C_RUN] = run
    st[C_SPANS] = 0
    st[C_SKIP] = si
    return p


def halve(cnt, top, cnt2, top2):
    """The next overview level: two buckets -> one (most sounding, highest track)."""
    n = cnt.shape[1]
    for k in range(cnt.shape[0]):
        for g in range(cnt2.shape[1]):
            a = 2 * g
            c = cnt[k, a]
            t = top[k, a]
            if a + 1 < n:
                if cnt[k, a + 1] > c:
                    c = cnt[k, a + 1]
                if top[k, a + 1] > t:
                    t = top[k, a + 1]
            cnt2[k, g] = c
            top2[k, g] = t
