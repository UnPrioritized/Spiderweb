//! Playing a loaded MIDI (files/midi_play.py, compiled there by fastloops as play_read, play_programs, play_order,
//! play_order_count, play_order_place, play_hist, play_stamp, play_condense, play_stamp_pre, play_emit): the very
//! same steps. Kinds as the program hands them over (play_loaded / play_dense): messages int32 (uint32 once
//! condensed), tracks int32, mutes uint16, frames uint64.
use crate::maths::fmodi;
use crate::NdMut;

const PS_TICK: usize = 0;
const PS_RUN: usize = 1;
const PS_BANK: usize = 2;
const PS_PTICK: usize = 18;
const PS_PVAL: usize = 34;
const PS_BTICK: usize = 50;
const COPY_TABLE: i64 = 1 << 20;
const COPY_PROBES: usize = 64;

#[inline(always)]
fn u(i: i64) -> usize {
    i as usize
}

/// Reads / writes without a check a step: only where the sizes were checked once before the loop.
#[inline(always)]
fn g<T: Copy>(s: &[T], i: usize) -> T {
    unsafe { *s.get_unchecked(i) }
}
#[inline(always)]
fn put<T>(s: &mut [T], i: usize, v: T) {
    unsafe { *s.get_unchecked_mut(i) = v }
}

/// A track's next variable-length number at q (moved on): (value, q, whole).
#[inline(always)]
fn varlen(b: &[u8], mut q: i64, end: i64) -> (i64, i64, bool) {
    let mut d = 0i64;
    loop {
        if q >= end {
            return (d, q, false);
        }
        let c = b[u(q)] as i64;
        q += 1;
        d = (d << 7) | (c & 127);
        if c < 128 {
            return (d, q, true);
        }
    }
}

/// midi_play.read -> n.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn read(b: &[u8], at: &[i64], size: &[i64], t1: i64, tick: &mut [i64], run: &mut [i64], nxt: &mut [i64],
            used: &mut [i64], kn: &[i32], out_t: &mut [i64], out_m: &mut [i32], out_k: &mut [i32], n: i64,
            base: &[i64], sk: &[i64], sk_i: &mut [i64], sk_end: &[i64], count: bool, opened: &mut [i64],
            slot: &[i64]) -> i64 {
    let mut n = n;
    for j in 0..at.len() {
        let mut p = at[j];
        let end = at[j] + size[j];
        let mut tk = tick[j];
        let mut rn = run[j];
        nxt[j] = -1;
        let mut si = sk_i[j];
        let se = sk_end[j];
        while p < end {
            let pos = base[j] + p - at[j];
            while si < se && sk[u(si)] < pos {
                si += 1;
            }
            let hid = si < se && sk[u(si)] == pos;
            let (d, mut q, mut whole) = varlen(b, p, end);
            if whole && q >= end {
                whole = false;
            }
            if !whole {
                break;
            }
            let t = tk + d;
            if t >= t1 {
                nxt[j] = t;
                break;
            }
            let mut s = b[u(q)] as i64;
            if s >= 128 {
                q += 1;
            } else {
                s = rn;
            }
            if s < 0xF0 {
                let hi = s >> 4;
                let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
                if q + need > end {
                    break;
                }
                if s >= 128 {
                    rn = s;
                }
                let a = b[u(q)] as i64;
                let v = if need == 2 { b[u(q + 1)] as i64 } else { 0 };
                let mut keep = true;
                if (hi == 0x8 || hi == 0x9 || hi == 0xA) && a >= 128 {
                    keep = false;
                }
                if hid && (hi == 0x8 || hi == 0x9) {
                    keep = false;
                }
                if count && keep && (hi == 0x8 || hi == 0x9 || hi == 0xA) {
                    if hi != 0xA {
                        let ln = u(slot[j] * 2048 + (s & 15) * 128 + (a & 127));
                        if hi == 0x9 && v > 0 {
                            opened[ln] += 1;
                        } else if opened[ln] > 0 {
                            opened[ln] -= 1;
                        }
                    }
                    keep = false;
                }
                if keep {
                    let k = u(n);
                    out_t[k] = t;
                    out_m[k] = (s | (a & 127) << 8 | (v & 127) << 16) as i32;
                    out_k[k] = kn[j];
                    n += 1;
                }
                q += need;
            } else {
                if s == 0xFF {
                    if q >= end {
                        break;
                    }
                    q += 1;
                }
                let (ln, q2, whole) = varlen(b, q, end);
                q = q2 + ln;
                if !whole || q > end {
                    break;
                }
            }
            tk = t;
            p = q;
        }
        tick[j] = tk;
        run[j] = rn;
        used[j] = p - at[j];
        sk_i[j] = si;
    }
    n
}

/// midi_play.programs -> where it stopped.
#[inline(always)]
pub fn programs(b: &[u8], p: i64, end: i64, st: &mut [i64]) -> i64 {
    let mut p = p;
    let mut tk = st[PS_TICK];
    let mut rn = st[PS_RUN];
    while p < end {
        let (d, mut q, whole) = varlen(b, p, end);
        if !whole || q >= end {
            break;
        }
        let mut s = b[u(q)] as i64;
        if s >= 128 {
            q += 1;
        } else {
            s = rn;
        }
        if s < 0xF0 {
            let hi = s >> 4;
            let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
            if q + need > end {
                break;
            }
            if s >= 128 {
                rn = s;
            }
            let ch = u(s & 15);
            let a = (b[u(q)] as i64) & 127;
            if hi == 0xB && need == 2 {
                let v = (b[u(q + 1)] as i64) & 127;
                if a == 0 {
                    st[PS_BANK + ch] = (st[PS_BANK + ch] & !127i64) | v;
                    st[PS_BTICK + ch] = tk + d + 1;
                } else if a == 32 {
                    st[PS_BANK + ch] = (st[PS_BANK + ch] & 127) | (v << 7);
                    st[PS_BTICK + ch] = tk + d + 1;
                }
            } else if hi == 0xC {
                st[PS_PTICK + ch] = tk + d + 1;
                st[PS_PVAL + ch] = a | (st[PS_BANK + ch] << 7);
            }
            q += need;
        } else {
            if s == 0xFF {
                if q >= end {
                    break;
                }
                q += 1;
            }
            let (ln, q2, whole) = varlen(b, q, end);
            q = q2 + ln;
            if !whole || q > end {
                break;
            }
        }
        tk += d;
        p = q;
    }
    st[PS_TICK] = tk;
    st[PS_RUN] = rn;
    p
}

/// midi_play.order (a counting sort) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn order(ticks: &[i64], ms: &[i32], ks: &[i32], base: i64, span: i64, out_t: &mut [i64], out_m: &mut [i32],
             out_k: &mut [i32]) -> i64 {
    let Some(mut count) = crate::try_zeros::<i64>(u(span + 1)) else {
        return crate::NO_MEMORY;
    };
    for &t in ticks {
        count[u(t - base + 1)] += 1;
    }
    for k in 0..u(span) {
        count[k + 1] += count[k];
    }
    for i in 0..ticks.len() {
        let k = u(ticks[i] - base);
        let j = count[k];
        out_t[u(j)] = ticks[i];
        out_m[u(j)] = ms[i];
        out_k[u(j)] = ks[i];
        count[k] = j + 1;
    }
    0
}

/// midi_play.order_count -> 0.
#[inline(always)]
pub fn order_count(ticks: &[i64], base: i64, count: &mut [i64]) -> i64 {
    for &t in ticks {
        count[u(t - base)] += 1;
    }
    0
}

/// midi_play.order_place -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn order_place(ticks: &[i64], ms: &[i32], ks: &[i32], base: i64, at: &mut [i64], out_t: &mut [i64],
                   out_m: &mut [i32], out_k: &mut [i32]) -> i64 {
    for i in 0..ticks.len() {
        let k = u(ticks[i] - base);
        let j = at[k];
        out_t[u(j)] = ticks[i];
        out_m[u(j)] = ms[i];
        out_k[u(j)] = ks[i];
        at[k] = j + 1;
    }
    0
}

/// midi_play.hist -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn hist(ms: &[i32], trk: &[i32], i0: i64, i1: i64, ticks: &[i64], t_late: i64, muted: &[u16], tport: &[i64],
            ports: i64, synths: i64, h: NdMut<i64, 2>) -> i64 {
    let hw = h.sh[1];
    let hd = h.d;
    let exact = ports < 0;
    let per = if exact || ports >= synths { 1 } else { crate::maths::fdiv(synths, ports) };
    for i in i0..i1 {
        let iu = u(i);
        let m = ms[iu] as i64;
        if (m & 0xF0) != 0x90 {
            continue;
        }
        let v = (m >> 16) & 127;
        let tr = u(trk[iu] as i64);
        if v == 0 || ((muted[tr] as i64) >> (m & 15)) & 1 != 0 || ticks[iu] < t_late {
            continue;
        }
        let port = tport[tr];
        let ch = m & 15;
        let r = if exact {
            port
        } else if ports >= synths {
            fmodi(port + ch, synths)
        } else {
            fmodi(port + ch, ports) * per + fmodi(((m >> 8) & 127) * 5 + ch * 3, per)
        };
        hd[u(r) * hw + u(v)] += 1;
    }
    0
}

/// The copy table's first place for a key (as `(int(key) * 2654435761) & (COPY_TABLE - 1)` in int64).
#[inline(always)]
fn slot_of(key: u32) -> i64 {
    (key as i64).wrapping_mul(2654435761) & (COPY_TABLE - 1)
}

/// A frame: base + rate x song seconds at tick t (tempo map entry j).
#[inline(always)]
fn frame(base: f64, rate: f64, ms_: &[f64], mt: &[i64], mspt: &[f64], j: usize, t: i64) -> f64 {
    let f = base + rate * (ms_[j] + ((t - mt[j]) as f64) * mspt[j]);
    if f < 0.0 {
        0.0
    } else {
        f
    }
}

/// np.uint64(x + 0.5) of a frame (never below 0 here).
#[inline(always)]
fn frame_u64(least: f64, f: f64) -> u64 {
    ((if f < least { least } else { f }) + 0.5) as u64
}

/// The line's next free node (Line: st = [first free, -, next never used, sounding]).
#[inline(always)]
fn new_node(st: &mut [i64], nxt: &[i64]) -> i64 {
    let k = st[0];
    if k >= 0 {
        st[0] = nxt[u(k)];
        k
    } else {
        let k = st[2];
        st[2] += 1;
        k
    }
}

/// midi_play.stamp -> how many.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn stamp(ms: &[i32], trk: &[i32], i0: i64, i1: i64, ticks: &[i64], muted: &[u16], tport: &[i64], mt: &[i64],
             ms_: &[f64], mspt: &[f64], base: f64, rate: f64, least: f64, fr: &mut [u64], out: &mut [u32],
             tr: &mut [u16], counts: &mut [i64], cp: &mut [u32], tab_key: &mut [u32], tab_gen: &mut [i64],
             tab_at: &mut [i64], head: &mut [i64], tail: &mut [i64], val: &mut [i64], nxt: &mut [i64],
             st: &mut [i64]) -> i64 {
    let mut n: i64 = 0;
    let mut j = 0usize;
    if i0 >= 0 && u(i0) < ticks.len() {
        let t0 = ticks[u(i0)];
        while j + 1 < mt.len() && mt[j + 1] <= t0 {
            j += 1;
        }
    }
    let mut last_t = -1i64;
    'ev: for i in i0..i1 {
        let iu = u(i);
        let t = ticks[iu];
        if t != last_t {
            last_t = t;
            counts[2] += 1;
        }
        while j + 1 < mt.len() && mt[j + 1] <= t {
            j += 1;
        }
        let f = frame(base, rate, ms_, mt, mspt, j, t);
        let m = ms[iu] as i64;
        let hi = (m & 0xFF) >> 4;
        let trk_i = u(trk[iu] as i64);
        let port = tport[trk_i];
        let p16 = fmodi(port, 16);
        if hi == 0x8 || hi == 0x9 {
            let q = u((p16 * 16 + (m & 15)) * 128 + ((m >> 8) & 127));
            if hi == 0x9 && (m >> 16) & 127 > 0 {
                let mut d: i64 = 1;
                let mut hh: i64 = -1;
                if ((muted[trk_i] as i64) >> (m & 15)) & 1 == 0 {
                    counts[0] += 1;
                    if f >= least {
                        counts[1] += 1;
                        let key = ((m & 0xFFFFFF) as u32) | ((p16 as u32) << 24);
                        let mut h = slot_of(key);
                        let mut found: i64 = -1;
                        for _ in 0..COPY_PROBES {
                            let hu = u(h);
                            if tab_gen[hu] != counts[2] {
                                tab_gen[hu] = counts[2];
                                tab_key[hu] = key;
                                hh = h;
                                break;
                            }
                            if tab_key[hu] == key {
                                found = tab_at[hu];
                                hh = h;
                                break;
                            }
                            h = (h + 1) & (COPY_TABLE - 1);
                        }
                        if found >= 0 && val[u(found >> 32)] < 0 {
                            let mut w = found >> 32;
                            for _ in 0..COPY_PROBES {
                                if w == tail[q] || w < 0 {
                                    break;
                                }
                                w = nxt[u(w)];
                            }
                            if w >= 0 && w == tail[q] {
                                let c = u(found & 0xFFFFFFFF);
                                cp[c] = cp[c].wrapping_add(1);
                                val[u(found >> 32)] -= 1;
                                continue 'ev;
                            }
                        }
                        d = -1;
                    }
                }
                let b = tail[q];
                if b >= 0 && d > 0 && val[u(b)] > 0 {
                    val[u(b)] += d;
                } else {
                    let k = new_node(st, nxt);
                    val[u(k)] = d;
                    nxt[u(k)] = -1;
                    if b >= 0 {
                        nxt[u(b)] = k;
                    } else {
                        head[q] = k;
                    }
                    tail[q] = k;
                    if d < 0 && hh >= 0 {
                        tab_at[u(hh)] = n | (k << 32);
                    }
                }
                if d > 0 {
                    continue;
                }
                st[3] += 1;
            } else {
                let k = head[q];
                if k >= 0 {
                    let ku = u(k);
                    let v = val[ku];
                    val[ku] = if v > 0 { v - 1 } else { v + 1 };
                    if val[ku] == 0 {
                        head[q] = nxt[ku];
                        if nxt[ku] < 0 {
                            tail[q] = -1;
                        }
                        nxt[ku] = st[0];
                        st[0] = k;
                    }
                    if v > 0 || v < -1 {
                        continue;
                    }
                    st[3] -= 1;
                }
            }
        }
        let nu = u(n);
        fr[nu] = frame_u64(least, f);
        out[nu] = ((m & 0xFFFFFF) as u32) | ((p16 as u32) << 24);
        tr[nu] = ((m & 15) | p16 << 4) as u16;
        cp[nu] = 1;
        n += 1;
    }
    n
}

/// midi_play.condense -> n.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn condense(ts: &[i64], ms: &[i32], ks: &[i32], tport: &[i64], n: i64, out_t: &mut [i64], out_m: &mut [u32],
                out_tr: &mut [u16], out_cp: &mut [u32], tab_key: &mut [u32], tab_gen: &mut [i64],
                tab_at: &mut [i64], counts: &mut [i64], last_on: &mut [i64]) -> i64 {
    let mut n = n;
    let len = ts.len();
    let room = out_t.len().min(out_m.len()).min(out_tr.len()).min(out_cp.len());
    let table = COPY_TABLE as usize;
    if n < 0 || ms.len() < len || ks.len() < len || u(n) + len > room || tab_key.len() < table
        || tab_gen.len() < table || tab_at.len() < table || last_on.len() < 32768 || counts.len() < 4 {
        return crate::FAILED; // (arrays too short: Numba's loop runs instead)
    }
    // (checked once above: h is masked below COPY_TABLE, q below 32768 (port % 16, channel, key), n stays
    // below room; the rest reads without a check a step. found / ks keep theirs: they come from the data)
    let (mut gen, mut last_t) = (counts[2], counts[3]);
    let mut ons = 0i64;
    for i in 0..len {
        let t = g(ts, i);
        if t != last_t {
            last_t = t;
            gen += 1;
        }
        let m = g(ms, i) as i64;
        let hi = (m & 0xFF) >> 4;
        let port = tport[u(g(ks, i) as i64)];
        let p16 = fmodi(port, 16);
        if hi == 0x8 || hi == 0x9 {
            let q = u((p16 * 16 + (m & 15)) * 128 + ((m >> 8) & 127));
            let key = ((m & 0xFFFFFF) as u32) | ((p16 as u32) << 24);
            let mut h = slot_of(key);
            let mut found: i64 = -1;
            for _ in 0..COPY_PROBES {
                let hu = u(h);
                if g(tab_gen, hu) != gen {
                    put(tab_gen, hu, gen);
                    put(tab_key, hu, key);
                    put(tab_at, hu, n);
                    break;
                }
                if g(tab_key, hu) == key {
                    found = g(tab_at, hu);
                    break;
                }
                h = (h + 1) & (COPY_TABLE - 1);
            }
            if hi == 0x9 && (m >> 16) & 127 > 0 {
                ons += 1;
                if found >= 0 {
                    let f = u(found);
                    out_cp[f] = out_cp[f].wrapping_add(1);
                    continue;
                }
                put(last_on, q, n);
            } else {
                if found >= 0 && found > g(last_on, q) {
                    let f = u(found);
                    out_cp[f] = out_cp[f].wrapping_add(1);
                    continue;
                }
                if found >= 0 {
                    put(tab_at, u(h), n);
                }
            }
        }
        let nu = u(n);
        put(out_t, nu, t);
        put(out_m, nu, ((m & 0xFFFFFF) as u32) | ((p16 as u32) << 24));
        put(out_tr, nu, ((m & 15) | p16 << 4) as u16);
        put(out_cp, nu, 1);
        n += 1;
    }
    counts[0] += ons;
    counts[2] = gen;
    counts[3] = last_t;
    n
}

/// midi_play.stamp_pre -> how many.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn stamp_pre(ts: &[i32], ms: &[u32], trs: &[u16], cps: &[u32], t0: i64, i0: i64, i1: i64, mt: &[i64],
                 ms_: &[f64], mspt: &[f64], base: f64, rate: f64, least: f64, fr: &mut [u64], out: &mut [u32],
                 tr: &mut [u16], cp: &mut [u32], counts: &mut [i64], dry: bool, head: &mut [i64],
                 tail: &mut [i64], val: &mut [i64], nxt: &mut [i64], st: &mut [i64]) -> i64 {
    let mut n: i64 = 0;
    let mut j = 0usize;
    if i0 >= 0 && u(i0) < ts.len() {
        let first = ts[u(i0)] as i64 + t0;
        while j + 1 < mt.len() && mt[j + 1] <= first {
            j += 1;
        }
    }
    for i in i0..i1 {
        let iu = u(i);
        let t = ts[iu] as i64 + t0;
        while j + 1 < mt.len() && mt[j + 1] <= t {
            j += 1;
        }
        let f = frame(base, rate, ms_, mt, mspt, j, t);
        let m = ms[iu] as i64;
        let hi = (m & 0xFF) >> 4;
        if hi == 0x8 || hi == 0x9 {
            let q = u((((m >> 24) & 15) * 16 + (m & 15)) * 128 + ((m >> 8) & 127));
            let mut c = cps[iu] as i64;
            if hi == 0x9 && (m >> 16) & 127 > 0 {
                counts[0] += c;
                let sent = !dry && f >= least;
                if sent {
                    counts[1] += c;
                    st[3] += 1;
                }
                let d = if sent { -c } else { c };
                let b = tail[q];
                if b >= 0 && d > 0 && val[u(b)] > 0 {
                    val[u(b)] += d;
                } else {
                    let k = new_node(st, nxt);
                    val[u(k)] = d;
                    nxt[u(k)] = -1;
                    if b >= 0 {
                        nxt[u(b)] = k;
                    } else {
                        head[q] = k;
                    }
                    tail[q] = k;
                }
                if !sent {
                    continue;
                }
            } else {
                let mut s: i64 = 0;
                while c > 0 && head[q] >= 0 {
                    let k = head[q];
                    let ku = u(k);
                    let v = val[ku];
                    let take;
                    if v > 0 {
                        take = if v < c { v } else { c };
                        val[ku] = v - take;
                    } else {
                        take = if -v < c { -v } else { c };
                        val[ku] = v + take;
                        if val[ku] == 0 {
                            s += 1;
                        }
                    }
                    c -= take;
                    if val[ku] == 0 {
                        head[q] = nxt[ku];
                        if nxt[ku] < 0 {
                            tail[q] = -1;
                        }
                        nxt[ku] = st[0];
                        st[0] = k;
                    }
                }
                st[3] -= s;
                if s == 0 || dry {
                    continue;
                }
                let g = frame_u64(least, f);
                for _ in 0..s {
                    let nu = u(n);
                    fr[nu] = g;
                    out[nu] = m as u32;
                    tr[nu] = trs[iu];
                    cp[nu] = 1;
                    n += 1;
                }
                continue;
            }
        }
        if dry {
            continue;
        }
        let nu = u(n);
        fr[nu] = frame_u64(least, f);
        out[nu] = m as u32;
        tr[nu] = trs[iu];
        cp[nu] = cps[iu];
        n += 1;
    }
    n
}

/// midi_play.emit -> where it stopped.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn emit(ms: &[i32], trk: &[i32], i0: i64, i1: i64, ticks: &[i64], t_late: i64, muted: &[u16], tport: &[i64],
            ports: i64, synths: i64, thr: &[i64], room: &mut [i64], out: NdMut<u8, 2>, lens: &mut [i64],
            three: bool, head: &mut [i64], tail: &mut [i64], val: &mut [i64], nxt: &mut [i64], st: &mut [i64])
            -> i64 {
    let cap = out.sh[1] as i64;
    let od = out.d;
    let exact = ports < 0;
    let per = if exact || ports >= synths { 1 } else { crate::maths::fdiv(synths, ports) };
    for i in i0..i1 {
        let iu = u(i);
        let m = ms[iu] as i64;
        let s = m & 0xFF;
        let hi = s >> 4;
        let ch = s & 15;
        let trk_i = u(trk[iu] as i64);
        let port = tport[trk_i];
        let size = if (hi == 0xC || hi == 0xD) && !three { 2 } else { 3 };
        if hi == 0x8 || hi == 0x9 || hi == 0xA {
            let key = (m >> 8) & 127;
            let r = if exact {
                port
            } else if ports >= synths {
                fmodi(port + ch, synths)
            } else {
                fmodi(port + ch, ports) * per + fmodi(key * 5 + ch * 3, per)
            };
            let ru = u(r);
            if lens[ru] + 3 > cap {
                return i;
            }
            let q = u((fmodi(port, 64) * 16 + ch) * 128 + key);
            let v = (m >> 16) & 127;
            if hi == 0x9 && v > 0 {
                let mut go = ((muted[trk_i] as i64) >> ch) & 1 == 0 && ticks[iu] >= t_late;
                if go && v < thr[ru] {
                    go = v == thr[ru] - 1 && room[ru] > 0;
                    if go {
                        room[ru] -= 1;
                    }
                }
                let d: i64 = if go { -1 } else { 1 };
                let b = tail[q];
                if b >= 0 && (val[u(b)] > 0) == (d > 0) {
                    val[u(b)] += d;
                } else {
                    let k = new_node(st, nxt);
                    val[u(k)] = d;
                    nxt[u(k)] = -1;
                    if b >= 0 {
                        nxt[u(b)] = k;
                    } else {
                        head[q] = k;
                    }
                    tail[q] = k;
                }
                if !go {
                    continue;
                }
            } else if hi != 0xA {
                let k = head[q];
                if k >= 0 {
                    let ku = u(k);
                    let v = val[ku];
                    val[ku] = if v > 0 { v - 1 } else { v + 1 };
                    if val[ku] == 0 {
                        head[q] = nxt[ku];
                        if nxt[ku] < 0 {
                            tail[q] = -1;
                        }
                        nxt[ku] = st[0];
                        st[0] = k;
                    }
                    if v > 0 {
                        continue;
                    }
                }
            }
            let o = lens[ru];
            let at = ru * u(cap) + u(o);
            od[at] = s as u8;
            od[at + 1] = ((m >> 8) & 127) as u8;
            od[at + 2] = ((m >> 16) & 127) as u8;
            lens[ru] = o + 3;
        } else {
            let first = if exact {
                port
            } else if ports < synths {
                fmodi(port + ch, ports) * per
            } else {
                fmodi(port + ch, synths)
            };
            for r in first..first + per {
                if lens[u(r)] + size > cap {
                    return i;
                }
            }
            for r in first..first + per {
                let ru = u(r);
                let o = lens[ru];
                let at = ru * u(cap) + u(o);
                od[at] = s as u8;
                od[at + 1] = ((m >> 8) & 127) as u8;
                if size == 3 {
                    od[at + 2] = ((m >> 16) & 127) as u8;
                }
                lens[ru] = o + size;
            }
        }
    }
    i1
}
