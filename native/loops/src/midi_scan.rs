//! Load MIDI's byte loops (files/midi_scan.py, compiled there by fastloops as midi_walk, midi_rewrite,
//! midi_note_ticks, midi_busy_hits, midi_gather): the very same steps, byte by byte.
use crate::maths::{f2i, fdiv, imax, imin, rint};

const ST_TICK: usize = 0;
const ST_RUN: usize = 1;
const ST_EOT: usize = 2;
const ST_FLAGS: usize = 3;
const ST_AFTER: usize = 4;
const ST_META: usize = 5;
const ST_GOOD: usize = 6;
const BAD: i64 = 1;
const CUT: i64 = 2;
const LONG_GAP: i64 = 8;
const RUN_AFTER_META: i64 = 16;
const AFTER_EOT: i64 = 32;
const ODD_TEMPO: i64 = 64;
const HIGH_DATA: i64 = 128;
const MOST_TICK: i64 = 1 << 48;
const C_EVENTS: usize = 0;
const C_ON: usize = 1;
const C_OFF: usize = 2;
const C_ON0: usize = 3;
const C_POLY: usize = 4;
const C_CC: usize = 5;
const C_PROG: usize = 6;
const C_PRESS: usize = 7;
const C_BEND: usize = 8;
const C_TEMPO: usize = 9;
const C_META: usize = 10;
const C_SYSEX: usize = 11;
const C_CHANNELS: usize = 12;
const C_PROGS: usize = 13;
const C_KITS: usize = 15;
const C_BANK: usize = 17;
const C_PTICK: usize = 33;
const C_PVAL: usize = 49;
const C_BTICK: usize = 65;

/// midi_scan.walk -> where it stopped.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn walk(b: &[u8], p: i64, end: i64, final_: bool, rest: i64, st: &mut [i64], cnt: &mut [i64]) -> i64 {
    let mut p = p;
    let mut tick = st[ST_TICK];
    let mut run = st[ST_RUN];
    let mut flags = st[ST_FLAGS];
    let mut meta = st[ST_META];
    let mut good;
    let at = |q: i64| b[q as usize] as i64;
    loop {
        good = p;
        if st[ST_EOT] != 0 {
            if end > p {
                st[ST_AFTER] += end - p;
                flags |= AFTER_EOT;
            }
            p = end;
            break;
        }
        if p >= end {
            break;
        }
        let mut q = p;
        let mut d = 0i64;
        let mut n = 0;
        let mut whole = true;
        let mut beyond = false;
        loop {
            if q >= end {
                whole = false;
                break;
            }
            let c = at(q);
            q += 1;
            d = (d << 7) | (c & 127);
            n += 1;
            if c < 128 {
                break;
            }
            if n == 8 {
                flags |= BAD;
                break;
            }
        }
        if flags & BAD != 0 {
            p = end;
            break;
        }
        if whole && q >= end {
            whole = false;
        }
        if whole {
            if n > 4 {
                flags |= LONG_GAP;
            }
            let mut s = at(q);
            let explicit = s >= 128;
            if explicit {
                q += 1;
            } else if run == 0 {
                flags |= BAD;
                p = end;
                break;
            } else {
                s = run;
                if meta != 0 {
                    flags |= RUN_AFTER_META;
                }
            }
            if s < 0xF0 {
                let hi = s >> 4;
                let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
                if q + need > end {
                    whole = false;
                    beyond = q + need > end + rest;
                } else {
                    if at(q) >= 128 || (need == 2 && at(q + 1) >= 128) {
                        flags |= HIGH_DATA;
                    }
                    let ch = (s & 15) as usize;
                    if hi == 0x9 {
                        if at(q + 1) > 0 {
                            cnt[C_ON] += 1;
                        } else {
                            cnt[C_ON0] += 1;
                        }
                    } else if hi == 0x8 {
                        cnt[C_OFF] += 1;
                    } else if hi == 0xA {
                        cnt[C_POLY] += 1;
                    } else if hi == 0xB {
                        cnt[C_CC] += 1;
                        if at(q) == 0 {
                            cnt[C_BANK + ch] = (cnt[C_BANK + ch] & !127i64) | (at(q + 1) & 127);
                            cnt[C_BTICK + ch] = tick + d + 1;
                        } else if at(q) == 32 {
                            cnt[C_BANK + ch] = (cnt[C_BANK + ch] & 127) | ((at(q + 1) & 127) << 7);
                            cnt[C_BTICK + ch] = tick + d + 1;
                        }
                    } else if hi == 0xC {
                        cnt[C_PROG] += 1;
                        let pg = at(q) & 127;
                        let bits = if ch == 9 { C_KITS } else { C_PROGS };
                        cnt[bits + (pg >> 6) as usize] |= 1i64 << (pg & 63);
                        cnt[C_PTICK + ch] = tick + d + 1;
                        cnt[C_PVAL + ch] = pg | (cnt[C_BANK + ch] << 7);
                    } else if hi == 0xD {
                        cnt[C_PRESS] += 1;
                    } else {
                        cnt[C_BEND] += 1;
                    }
                    cnt[C_CHANNELS] |= 1i64 << ch;
                    if explicit {
                        run = s;
                    }
                    q += need;
                    meta = 0;
                }
            } else if s == 0xFF || s == 0xF0 || s == 0xF7 {
                let mut mt = -1i64;
                if s == 0xFF {
                    if q >= end {
                        whole = false;
                    } else {
                        mt = at(q);
                        q += 1;
                    }
                }
                if whole {
                    let mut ln = 0i64;
                    let mut m = 0;
                    loop {
                        if q >= end {
                            whole = false;
                            break;
                        }
                        let c = at(q);
                        q += 1;
                        ln = (ln << 7) | (c & 127);
                        m += 1;
                        if c < 128 {
                            break;
                        }
                        if m == 8 {
                            flags |= BAD;
                            break;
                        }
                    }
                    if flags & BAD != 0 {
                        p = end;
                        break;
                    }
                    if whole && q + ln > end {
                        whole = false;
                        beyond = q + ln > end + rest;
                    }
                    if whole {
                        if m > 4 {
                            flags |= LONG_GAP;
                        }
                        if s != 0xFF {
                            cnt[C_SYSEX] += 1;
                        } else if mt == 0x51 {
                            cnt[C_TEMPO] += 1;
                            if ln != 3 {
                                flags |= ODD_TEMPO;
                            }
                        } else {
                            cnt[C_META] += 1;
                            if mt == 0x2F {
                                st[ST_EOT] = 1;
                            }
                        }
                        q += ln;
                        meta = 1;
                    }
                }
            } else {
                flags |= BAD;
                p = end;
                break;
            }
        }
        if !whole {
            if final_ || beyond {
                flags |= CUT;
                p = end;
            }
            break;
        }
        tick += d;
        if tick > MOST_TICK {
            flags |= BAD;
            p = end;
            break;
        }
        cnt[C_EVENTS] += 1;
        p = q;
    }
    st[ST_TICK] = tick;
    st[ST_RUN] = run;
    st[ST_FLAGS] = flags;
    st[ST_META] = meta;
    st[ST_GOOD] = if flags & (BAD | CUT) != 0 { good } else { p };
    p
}

const RW_TICK: usize = 0;
const RW_RUN: usize = 1;
const RW_DONE: usize = 2;
const RW_WRUN: usize = 3;
const RW_EOT: usize = 4;
const RW_INS: usize = 5;
const RW_SKIP: usize = 6;

/// A MIDI wait at out[o] -> the next o.
#[inline(always)]
fn put_gap(out: &mut [u8], mut o: usize, gap: i64) -> usize {
    let mut nb = 1;
    let mut x = gap >> 7;
    while x > 0 {
        nb += 1;
        x >>= 7;
    }
    for m in (0..nb).rev() {
        let mut v = (gap >> (7 * m)) & 127;
        if m > 0 {
            v |= 128;
        }
        out[o] = v as u8;
        o += 1;
    }
    o
}

/// midi_scan.rewrite; res = (where it stopped in b, where it stopped in out) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn rewrite(b: &[u8], p: i64, end: i64, _final: bool, bp_n: &[i64], bp_f: &[i64], use_map: bool, drop: &[u8],
               ins_t: &[i64], ins_at: &[i64], ins_b: &[u8], out: &mut [u8], o: i64, st: &mut [i64], base: i64,
               skip: &[i64], chan: i64, keep: i64, meta: i64, res: &mut [i64]) -> i64 {
    let (mut p, mut o) = (p, o as usize);
    let mut tick = st[RW_TICK];
    let mut run = st[RW_RUN];
    let mut done = st[RW_DONE];
    let mut wrun = st[RW_WRUN];
    let mut k = st[RW_INS] as usize;
    let mut si = st[RW_SKIP] as usize;
    let n_ins = ins_t.len();
    let room = out.len();
    let at = |q: i64| b[q as usize] as i64;
    while p < end {
        if st[RW_EOT] != 0 {
            let c = imin(end - p, room as i64 - o as i64).max(0) as usize;
            out[o..o + c].copy_from_slice(&b[p as usize..p as usize + c]);
            o += c;
            p += c as i64;
            break;
        }
        let mut q = p;
        let mut d = 0i64;
        let mut whole = true;
        loop {
            if q >= end {
                whole = false;
                break;
            }
            let c = at(q);
            q += 1;
            d = (d << 7) | (c & 127);
            if c < 128 {
                break;
            }
        }
        let gap_end = q;
        if whole && q >= end {
            whole = false;
        }
        let mut eot = false;
        let mut explicit = false;
        let mut mt = -1i64;
        let mut s = 0i64;
        if whole {
            s = at(q);
            explicit = s >= 128;
            if explicit {
                q += 1;
            } else {
                s = run;
            }
            if s < 0xF0 {
                q += if (s >> 4) == 0xC || (s >> 4) == 0xD { 1 } else { 2 };
                if q > end {
                    whole = false;
                }
            } else {
                if s == 0xFF {
                    if q >= end {
                        whole = false;
                    } else {
                        mt = at(q);
                        eot = mt == 0x2F;
                        q += 1;
                    }
                }
                let mut ln = 0i64;
                while whole {
                    if q >= end {
                        whole = false;
                        break;
                    }
                    let c = at(q);
                    q += 1;
                    ln = (ln << 7) | (c & 127);
                    if c < 128 {
                        break;
                    }
                }
                q += ln;
                if q > end {
                    whole = false;
                }
            }
        }
        if !whole {
            break;
        }
        let t = tick + d;
        let mut nt = t;
        if use_map {
            let (mut lo, mut hi) = (0usize, bp_n.len());
            while lo < hi {
                let mid = (lo + hi) / 2;
                if bp_n[mid] <= t {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }
            let i = (lo as i64 - 1).max(0) as usize;
            nt = bp_f[i] + (t - bp_n[i]);
            let j = (i + 1).min(bp_n.len() - 1);
            let gd = bp_n[j] - bp_n[i];
            let fd = bp_f[j] - bp_f[i];
            if j > i && t > bp_n[i] && gd > 0 && fd != gd {
                let mut share = bp_f[i] + f2i(rint(((t - bp_n[i]).wrapping_mul(fd)) as f64 / gd as f64));
                if share == bp_f[i] {
                    share = bp_f[i] + imin(fd, 1);
                }
                nt = share;
            }
        }
        let mut full = false;
        while k < n_ins && (ins_t[k] <= nt || eot) {
            let size = (ins_at[k + 1] - ins_at[k]) as usize;
            if o + 10 + size > room {
                full = true;
                break;
            }
            let gap = imax(ins_t[k] - done, 0);
            o = put_gap(out, o, gap);
            let at0 = o;
            for m in ins_at[k]..ins_at[k + 1] {
                out[o] = ins_b[m as usize];
                o += 1;
            }
            if chan >= 0 && o > at0 && (0x80..0xF0).contains(&out[at0]) {
                out[at0] = ((out[at0] as i64 & 0xF0) | chan) as u8;
            }
            done += gap;
            k += 1;
            wrun = -1;
        }
        if full {
            break;
        }
        if mt >= 0 && (mt as usize) < drop.len() && drop[mt as usize] != 0 {
            tick = t;
            p = q;
            continue;
        }
        while si < skip.len() && skip[si] < base + p {
            si += 1;
        }
        if si < skip.len() && skip[si] == base + p && s < 0xF0 {
            if explicit {
                run = s;
            }
            tick = t;
            p = q;
            continue;
        }
        if (s < 0xF0 && (keep >> (s & 15)) & 1 == 0) || (s >= 0xF0 && meta == 0 && !eot) {
            if explicit && s < 0xF0 {
                run = s;
            }
            tick = t;
            p = q;
            continue;
        }
        let mut dd = nt - done;
        if dd < 0 {
            dd = 0;
            nt = done;
        }
        let mut ws = s;
        if chan >= 0 && s < 0xF0 {
            ws = (s & 0xF0) | chan;
        }
        let add = (!explicit && s < 0xF0 && wrun != ws) as i64;
        let size = (if dd == d { gap_end - p } else { 10 }) + add + (q - gap_end);
        if o as i64 + size > room as i64 {
            break;
        }
        if dd == d {
            for m in p..gap_end {
                out[o] = b[m as usize];
                o += 1;
            }
        } else {
            o = put_gap(out, o, dd);
        }
        if add != 0 {
            out[o] = ws as u8;
            o += 1;
        }
        let at0 = o;
        for m in gap_end..q {
            out[o] = b[m as usize];
            o += 1;
        }
        if explicit && ws != s {
            out[at0] = ws as u8;
        }
        if s < 0xF0 {
            if explicit {
                run = s;
            }
            wrun = ws;
        }
        tick = t;
        done = nt;
        p = q;
        if eot {
            st[RW_EOT] = 1;
        }
    }
    st[RW_TICK] = tick;
    st[RW_RUN] = run;
    st[RW_DONE] = done;
    st[RW_WRUN] = wrun;
    st[RW_INS] = k as i64;
    st[RW_SKIP] = si as i64;
    res[0] = p;
    res[1] = o as i64;
    0
}

/// One event's gap + status read like note_ticks / busy_hits / gather: -> (q after the gap, d) or None (not whole).
#[inline(always)]
fn read_gap(b: &[u8], p: i64, end: i64) -> Option<(i64, i64)> {
    let mut q = p;
    let mut d = 0i64;
    loop {
        if q >= end {
            return None;
        }
        let c = b[q as usize] as i64;
        q += 1;
        d = (d << 7) | (c & 127);
        if c < 128 {
            break;
        }
    }
    if q >= end {
        return None;
    }
    Some((q, d))
}

/// A meta / SysEx event's body from q (the byte after its status) -> where it ends, or None (not whole).
#[inline(always)]
fn skip_meta(b: &[u8], s: i64, mut q: i64, end: i64) -> Option<i64> {
    if s == 0xFF {
        if q >= end {
            return None;
        }
        q += 1;
    }
    let mut ln = 0i64;
    let mut whole = true;
    loop {
        if q >= end {
            whole = false;
            break;
        }
        let c = b[q as usize] as i64;
        q += 1;
        ln = (ln << 7) | (c & 127);
        if c < 128 {
            break;
        }
    }
    q += ln;
    if !whole || q > end {
        return None;
    }
    Some(q)
}

/// midi_scan.note_ticks -> where it stopped.
#[inline(always)]
pub fn note_ticks(b: &[u8], p: i64, end: i64, _final: bool, mark: &mut [u8], base: i64, st: &mut [i64]) -> i64 {
    let mut p = p;
    let mut tick = st[0];
    let mut run = st[1];
    let top = base + mark.len() as i64;
    while p < end {
        let Some((mut q, d)) = read_gap(b, p, end) else { break };
        let mut s = b[q as usize] as i64;
        if s >= 128 {
            q += 1;
        } else {
            s = run;
        }
        if s < 0xF0 {
            q += if (s >> 4) == 0xC || (s >> 4) == 0xD { 1 } else { 2 };
            if q > end {
                break;
            }
            if s >= 128 {
                run = s;
            }
            let t = tick + d;
            if base <= t && t < top {
                mark[(t - base) as usize] |= if (s >> 4) == 0x8 || (s >> 4) == 0x9 { 1 } else { 2 };
            }
        } else {
            match skip_meta(b, s, q, end) {
                Some(q2) => q = q2,
                None => break,
            }
        }
        tick += d;
        p = q;
    }
    st[0] = tick;
    st[1] = run;
    p
}

/// midi_scan.busy_hits -> where it stopped.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn busy_hits(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], t_stop: i64, st: &mut [i64], cnt: &mut [i64],
                 since: &mut [i64], lane_at: &[i64], own_s: &[i64], own_e: &[i64], own_top: &[i64], hit: &mut [u8])
                 -> i64 {
    let mut p = p;
    let mut tick = st[0];
    let mut run = st[1];
    let mut si = st[3] as usize;
    while p < end {
        while si < skip.len() && skip[si] < base + p {
            si += 1;
        }
        let hid = si < skip.len() && skip[si] == base + p;
        let Some((mut q, d)) = read_gap(b, p, end) else { break };
        let t = tick + d;
        if t >= t_stop {
            st[2] = 1;
            break;
        }
        let mut s = b[q as usize] as i64;
        if s >= 128 {
            q += 1;
        } else {
            s = run;
        }
        if s < 0xF0 {
            let hi = s >> 4;
            let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
            if q + need > end {
                break;
            }
            if s >= 128 {
                run = s;
            }
            if (hi == 0x8 || hi == 0x9) && !hid {
                let ln = ((s & 15) * 256 + b[q as usize] as i64) as usize;
                if hi == 0x9 && b[q as usize + 1] > 0 {
                    if cnt[ln] == 0 {
                        since[ln] = t;
                    }
                    cnt[ln] += 1;
                } else if cnt[ln] > 0 {
                    cnt[ln] -= 1;
                    if cnt[ln] == 0 && t > since[ln] {
                        let a = since[ln];
                        let (mut lo, mut top) = (lane_at[ln], lane_at[ln + 1]);
                        while lo < top {
                            let mid = fdiv(lo + top, 2);
                            if own_s[mid as usize] < t {
                                lo = mid + 1;
                            } else {
                                top = mid;
                            }
                        }
                        let mut j = lo - 1;
                        while j >= lane_at[ln] && own_top[j as usize] > a {
                            if own_e[j as usize] > a {
                                hit[j as usize] = 1;
                            }
                            j -= 1;
                        }
                    }
                }
            }
            q += need;
        } else {
            match skip_meta(b, s, q, end) {
                Some(q2) => q = q2,
                None => break,
            }
        }
        tick = t;
        p = q;
    }
    st[0] = tick;
    st[1] = run;
    st[3] = si as i64;
    p
}

/// midi_scan.gather -> where it stopped.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn gather(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], t0: i64, t1: i64, k0: i64, k1: i64, chans: &[u8],
              st: &mut [i64], old: &mut [i64], newer: &mut [i64], head: &mut [i64], tail: &mut [i64],
              nxt: &mut [i64], o_s: &mut [i64], o_e: &mut [i64], o_k: &mut [i64], o_v: &mut [i64],
              o_on: &mut [i64], o_off: &mut [i64], o_was: &mut [u8]) -> i64 {
    let mut p = p;
    let mut tick = st[0];
    let mut run = st[1];
    let mut n = st[2] as usize;
    let mut si = st[4] as usize;
    let room = o_s.len();
    while p < end && st[5] == 0 && n < room {
        let Some((mut q, d)) = read_gap(b, p, end) else { break };
        let t = tick + d;
        if t >= t1 && st[3] == 0 {
            st[5] = 1;
            break;
        }
        while si < skip.len() && skip[si] < base + p {
            si += 1;
        }
        let hid = si < skip.len() && skip[si] == base + p;
        let mut s = b[q as usize] as i64;
        if s >= 128 {
            q += 1;
        } else {
            s = run;
        }
        if s < 0xF0 {
            let hi = s >> 4;
            let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
            if q + need > end {
                break;
            }
            if s >= 128 {
                run = s;
            }
            if hi == 0x8 || hi == 0x9 {
                let k = b[q as usize] as i64;
                let ch = s & 15;
                let ln = (ch * 256 + k) as usize;
                if chans[ch as usize] != 0 && k0 <= k && k <= k1 {
                    if hi == 0x9 && b[q as usize + 1] > 0 {
                        if t < t0 {
                            old[ln] += 1;
                        } else if t < t1 {
                            o_s[n] = t;
                            o_e[n] = -1;
                            o_k[n] = ln as i64;
                            o_v[n] = b[q as usize + 1] as i64;
                            o_on[n] = base + p;
                            o_off[n] = -1;
                            o_was[n] = hid as u8;
                            nxt[n] = -1;
                            if tail[ln] >= 0 {
                                nxt[tail[ln] as usize] = n as i64;
                            } else {
                                head[ln] = n as i64;
                            }
                            tail[ln] = n as i64;
                            n += 1;
                            st[3] += 1;
                        } else {
                            newer[ln] += 1;
                        }
                    } else if old[ln] > 0 {
                        old[ln] -= 1;
                    } else if head[ln] >= 0 {
                        let i = head[ln] as usize;
                        o_e[i] = t;
                        o_off[i] = base + p;
                        head[ln] = nxt[i];
                        if head[ln] < 0 {
                            tail[ln] = -1;
                        }
                        st[3] -= 1;
                    } else if newer[ln] > 0 {
                        newer[ln] -= 1;
                    }
                }
            }
            q += need;
        } else {
            match skip_meta(b, s, q, end) {
                Some(q2) => q = q2,
                None => break,
            }
        }
        tick = t;
        p = q;
    }
    st[0] = tick;
    st[1] = run;
    st[2] = n as i64;
    st[4] = si as i64;
    p
}
