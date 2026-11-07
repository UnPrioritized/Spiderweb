//! Load MIDI's index and pictures (files/midi_index.py, compiled there by fastloops as midi_index, midi_close,
//! midi_grid, midi_halve, midi_cover, midi_merge_loud; + fastloops' midi_cover_many, loaded_pixels, all_tops): the
//! very same steps.
use crate::maths::{f2i, fdiv, imax, imin};
use crate::pyext::{Out, Outs};
use crate::{Flag, Nd, NdMut};

const X_TICK: usize = 0;
const X_RUN: usize = 1;
const X_STOP: usize = 2;
const X_USED: usize = 3;
const X_FREE: usize = 4;
const X_NOPEN: usize = 5;
const X_NOTES: usize = 6;
const X_ZERO: usize = 7;
const X_ORPHAN: usize = 8;
const X_HANG: usize = 9;
const X_LASTCP: usize = 10;
const X_NCP: usize = 11;
const X_NENT: usize = 12;
const X_KLO: usize = 13;
const X_KHI: usize = 14;
const X_FIRST: usize = 15;
const X_END: usize = 16;
const X_NMETA: usize = 17;
const X_GLO: usize = 18;
const X_GHI: usize = 19;
const X_EOT: usize = 20;
const X_LONG: usize = 21;
const X_NCC: usize = 22;
const X_CCOVER: usize = 23;
const GO: i64 = 0;
const MORE_POOL: i64 = 1;
const MORE_CP: i64 = 2;
const MORE_ENT: i64 = 3;
const MORE_META: i64 = 4;
const MORE_GRID: i64 = 5;
const MORE_KEYS: i64 = 6;
const MORE_CC: i64 = 7;
const CC_KINDS: i64 = 132;
const K_PROGRAM: i64 = 128;
const K_BEND: i64 = 129;
const K_PRESSURE: i64 = 130;
const K_POLY: i64 = 131;
const CC_MOST: i64 = 20_000_000;
const CP_LEAST: i64 = 64 << 10;
const CP_PER_KEY: i64 = 4 << 10;
const META_KEEP: i64 = 64;
const GRID_MOST: i64 = 1 << 18;
const C_TICK: usize = 0;
const C_RUN: usize = 1;
const C_STOP: usize = 2;
const C_SPANS: usize = 3;
const C_SKIP: usize = 4;

/// midi_index.index -> (status, where it stopped).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn index(b: &[u8], p: i64, end: i64, at: i64, g_: i64, ix: &mut [i64], q_hd: &mut [i64], q_tl: &mut [i64],
             q_rl: &mut [i64], q_pos: &mut [i64], q_vel: &mut [i64], opened: &mut [i64], pt: &mut [i64],
             pn: &mut [i64], pw: &mut [i64], cp_off: &mut [i64], cp_tick: &mut [i64], cp_run: &mut [i64],
             cp_ent: &mut [i64], e_q: &mut [i64], e_cnt: &mut [i64], e_end: &mut [i64], e_vel: &mut [i64],
             e_nx: &mut [i64], cov: NdMut<i32, 2>, edg: NdMut<u8, 2>, chn: &mut [i64], m_tick: &mut [i64],
             m_type: &mut [i64], m_len: &mut [i64], m_data: NdMut<u8, 2>, c_tick: &mut [i64], c_code: &mut [i32],
             ccn: &mut [i64]) -> Outs {
    let mut p = p;
    let mut tick = ix[X_TICK];
    let mut run = ix[X_RUN];
    let (nk, ng) = (cov.sh[0] as i64, cov.sh[1] as i64);
    let ngu = cov.sh[1];
    let (cov, edg) = (cov.d, edg.d);
    let mw = m_data.sh[1];
    let m_data = m_data.d;
    let mut status = GO;
    let byte = |q: i64| b[q as usize] as i64;
    while p < end {
        if ix[X_STOP] != 0 || ix[X_EOT] != 0 {
            p = end;
            break;
        }
        let ev = p;
        let pos = at + ev;
        let nopen = ix[X_NOPEN];
        if pos - ix[X_LASTCP] >= imax(CP_LEAST, CP_PER_KEY * nopen) {
            let c = ix[X_NCP] as usize;
            let mut n = ix[X_NENT];
            if c + 1 >= cp_off.len() {
                status = MORE_CP;
                break;
            }
            if n + nopen > e_q.len() as i64 {
                status = MORE_ENT;
                break;
            }
            cp_off[c] = pos;
            cp_tick[c] = tick;
            cp_run[c] = run;
            cp_ent[c] = n;
            for j in 0..nopen as usize {
                let q = opened[j] as usize;
                let node = q_tl[q] as usize;
                let nu = n as usize;
                e_q[nu] = q as i64;
                e_cnt[nu] = q_rl[q];
                e_end[nu] = -1;
                e_vel[nu] = q_vel[q];
                e_nx[nu] = pw[node];
                pw[node] = n;
                n += 1;
            }
            cp_ent[c + 1] = n;
            ix[X_NCP] = c as i64 + 1;
            ix[X_NENT] = n;
            ix[X_LASTCP] = pos;
        }
        let mut q = p;
        let mut d = 0i64;
        while q < end {
            let c8 = byte(q);
            q += 1;
            d = (d << 7) | (c8 & 127);
            if c8 < 128 {
                break;
            }
        }
        if q >= end {
            ix[X_STOP] = 1;
            break;
        }
        let t = tick + d;
        let mut s = byte(q);
        let explicit = s >= 128;
        if explicit {
            q += 1;
        } else if run == 0 {
            ix[X_STOP] = 1;
            break;
        } else {
            s = run;
        }
        if s < 0xF0 {
            let hi = s >> 4;
            let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
            if q + need > end {
                ix[X_STOP] = 1;
                break;
            }
            if hi == 0x9 || hi == 0x8 {
                let k = byte(q);
                let v = byte(q + 1);
                let ch = s & 15;
                let qq = (ch * 256 + k) as usize;
                if k >= nk {
                    status = MORE_KEYS;
                    break;
                }
                let ku = k as usize;
                if hi == 0x9 && v > 0 {
                    let g = fdiv(t, g_);
                    let far = g >= GRID_MOST - 1;
                    if !far && g >= ng {
                        status = MORE_GRID;
                        break;
                    }
                    let mut node = ix[X_FREE];
                    if node >= 0 {
                        ix[X_FREE] = pn[node as usize];
                    } else {
                        node = ix[X_USED];
                        if node >= pt.len() as i64 {
                            status = MORE_POOL;
                            break;
                        }
                        ix[X_USED] = node + 1;
                    }
                    let nu = node as usize;
                    pt[nu] = t;
                    pn[nu] = -1;
                    pw[nu] = -1;
                    if q_tl[qq] >= 0 {
                        pn[q_tl[qq] as usize] = node;
                    } else {
                        q_hd[qq] = node;
                    }
                    q_tl[qq] = node;
                    q_rl[qq] += 1;
                    if q_rl[qq] == 1 {
                        q_vel[qq] = v;
                        q_pos[qq] = nopen;
                        opened[nopen as usize] = qq as i64;
                        ix[X_NOPEN] = nopen + 1;
                    }
                    if !far {
                        let gi = ku * ngu + g as usize;
                        cov[gi] = cov[gi].wrapping_add(1);
                        edg[gi] = 1;
                        if g < ix[X_GLO] {
                            ix[X_GLO] = g;
                        }
                    }
                    ix[X_NOTES] += 1;
                    chn[ch as usize] += 1;
                    if k < ix[X_KLO] {
                        ix[X_KLO] = k;
                    }
                    if k > ix[X_KHI] {
                        ix[X_KHI] = k;
                    }
                    if t < ix[X_FIRST] {
                        ix[X_FIRST] = t;
                    }
                } else if q_rl[qq] == 0 {
                    ix[X_ORPHAN] += 1;
                } else {
                    let node = q_hd[qq] as usize;
                    let s0 = pt[node];
                    let ee = if t > s0 { t } else { s0 + 1 };
                    let g1 = imin(fdiv(ee - 1, g_) + 1, GRID_MOST - 1);
                    let far = fdiv(s0, g_) >= GRID_MOST - 1;
                    if !far && g1 >= ng {
                        status = MORE_GRID;
                        break;
                    }
                    if t == s0 {
                        ix[X_ZERO] += 1;
                    }
                    if t - s0 > 65535 {
                        ix[X_LONG] += 1;
                    }
                    let mut w = pw[node];
                    while w >= 0 {
                        e_end[w as usize] = t;
                        w = e_nx[w as usize];
                    }
                    if !far {
                        let gi = ku * ngu + g1 as usize;
                        cov[gi] = cov[gi].wrapping_sub(1);
                        edg[gi - 1] = 1;
                        if g1 > ix[X_GHI] {
                            ix[X_GHI] = g1;
                        }
                    }
                    if ee > ix[X_END] {
                        ix[X_END] = ee;
                    }
                    q_hd[qq] = pn[node];
                    if q_hd[qq] < 0 {
                        q_tl[qq] = -1;
                    }
                    pn[node] = ix[X_FREE];
                    ix[X_FREE] = node as i64;
                    q_rl[qq] -= 1;
                    if q_rl[qq] == 0 {
                        let j = q_pos[qq];
                        let last = opened[(nopen - 1) as usize];
                        opened[j as usize] = last;
                        q_pos[last as usize] = j;
                        q_pos[qq] = -1;
                        ix[X_NOPEN] = nopen - 1;
                    }
                }
            } else {
                let kind = if hi == 0xB {
                    byte(q)
                } else if hi == 0xC {
                    K_PROGRAM
                } else if hi == 0xE {
                    K_BEND
                } else if hi == 0xD {
                    K_PRESSURE
                } else {
                    K_POLY
                };
                let c = ix[X_NCC];
                if c < CC_MOST {
                    if c >= c_tick.len() as i64 {
                        status = MORE_CC;
                        break;
                    }
                    c_tick[c as usize] = t;
                    let mut code = s | byte(q) << 8;
                    if need == 2 {
                        code |= byte(q + 1) << 16;
                    }
                    c_code[c as usize] = code as i32;
                    ix[X_NCC] = c + 1;
                } else {
                    ix[X_CCOVER] = 1;
                }
                ccn[((s & 15) * CC_KINDS + kind) as usize] += 1;
            }
            if explicit {
                run = s;
            }
            q += need;
        } else if s == 0xFF || s == 0xF0 || s == 0xF7 {
            let mut mt = -1i64;
            if s == 0xFF {
                if q >= end {
                    ix[X_STOP] = 1;
                    break;
                }
                mt = byte(q);
                q += 1;
            }
            let mut ln = 0i64;
            while q < end {
                let c8 = byte(q);
                q += 1;
                ln = (ln << 7) | (c8 & 127);
                if c8 < 128 {
                    break;
                }
            }
            if q + ln > end {
                ix[X_STOP] = 1;
                break;
            }
            if mt == 0x03 || mt == 0x21 || mt == 0x51 || mt == 0x58 || mt == 0x59 {
                let m = ix[X_NMETA];
                if m >= m_tick.len() as i64 {
                    status = MORE_META;
                    break;
                }
                let mu = m as usize;
                m_tick[mu] = t;
                m_type[mu] = mt;
                m_len[mu] = ln;
                for j in 0..imin(ln, META_KEEP).max(0) as usize {
                    m_data[mu * mw + j] = b[(q as usize) + j];
                }
                ix[X_NMETA] = m + 1;
            }
            q += ln;
            if mt == 0x2F {
                ix[X_EOT] = 1;
            }
        } else {
            ix[X_STOP] = 1;
            break;
        }
        tick = t;
        p = q;
    }
    ix[X_TICK] = tick;
    ix[X_RUN] = run;
    Ok(vec![Out::Int(status), Out::Int(p)])
}

/// midi_index.close -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn close(ix: &mut [i64], g_: i64, q_hd: &[i64], q_rl: &mut [i64], opened: &[i64], pt: &[i64], pn: &[i64],
             pw: &[i64], e_end: &mut [i64], e_nx: &[i64], cov: NdMut<i32, 2>, edg: NdMut<u8, 2>) -> i64 {
    let ngu = cov.sh[1];
    let (cov, edg) = (cov.d, edg.d);
    let t = ix[X_TICK];
    for j in 0..ix[X_NOPEN].max(0) as usize {
        let qq = opened[j] as usize;
        let k = qq & 255;
        let mut node = q_hd[qq];
        while node >= 0 {
            let nu = node as usize;
            let s0 = pt[nu];
            let ee = if t > s0 { t } else { s0 + 1 };
            let g1 = imin(fdiv(ee - 1, g_) + 1, GRID_MOST - 1);
            let mut w = pw[nu];
            while w >= 0 {
                e_end[w as usize] = t;
                w = e_nx[w as usize];
            }
            if fdiv(s0, g_) < GRID_MOST - 1 {
                let gi = k * ngu + g1 as usize;
                cov[gi] = cov[gi].wrapping_sub(1);
                edg[gi - 1] = 1;
                if g1 > ix[X_GHI] {
                    ix[X_GHI] = g1;
                }
            }
            if ee > ix[X_END] {
                ix[X_END] = ee;
            }
            if t == s0 {
                ix[X_ZERO] += 1;
            }
            ix[X_HANG] += 1;
            node = pn[nu];
        }
        q_rl[qq] = 0;
    }
    ix[X_NOPEN] = 0;
    0
}

/// midi_index.grid -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn grid(cov: NdMut<i32, 2>, edg: NdMut<u8, 2>, glo: i64, ghi: i64, klo: i64, khi: i64, cnt: NdMut<i32, 2>,
            top: NdMut<i32, 2>, etop: NdMut<i32, 2>, track: i64, pic: NdMut<u8, 2>, pf: i64) -> i64 {
    let most = 2147483647i64;
    let ngu = cov.sh[1];
    let (cw, tw, ew, pw) = (cnt.sh[1], top.sh[1], etop.sh[1], pic.sh[1]);
    let (cov, edg, cnt, top, etop, pic) = (cov.d, edg.d, cnt.d, top.d, etop.d, pic.d);
    for k in klo..=khi {
        let ku = k as usize;
        let mut c = 0i64;
        for g in glo..=ghi {
            let gu = g as usize;
            let gi = ku * ngu + gu;
            c += cov[gi] as i64;
            cov[gi] = 0;
            if edg[gi] != 0 {
                edg[gi] = 0;
                if track > etop[ku * ew + gu] as i64 {
                    etop[ku * ew + gu] = track as i32;
                }
            }
            if c > 0 {
                let was = cnt[ku * cw + gu] as i64;
                cnt[ku * cw + gu] = (if c > most - was { most } else { was + c }) as i32;
                if track > top[ku * tw + gu] as i64 {
                    top[ku * tw + gu] = track as i32;
                }
                pic[(k - klo) as usize * pw + fdiv(g - glo, pf) as usize] = 1;
            }
        }
    }
    0
}

/// midi_index.halve -> 0.
#[inline(always)]
pub fn halve(cnt: Nd<i32, 2>, top: Nd<i32, 2>, cnt2: NdMut<i32, 2>, top2: NdMut<i32, 2>) -> i64 {
    let n = cnt.sh[1];
    let (w2, t2w) = (cnt2.sh[1], top2.sh[1]);
    let (rows, tw) = (cnt.sh[0], top.sh[1]);
    // (the usual shapes: rows checked once, then plain slices the compiler can run several at a time; any other
    // shape takes the step-by-step loop below, which reads exactly what Numba's does)
    if w2 > 0 && 2 * (w2 - 1) < n && tw >= n && t2w >= w2 && top.sh[0] >= rows && cnt2.sh[0] >= rows
        && top2.sh[0] >= rows {
        for k in 0..rows {
            let c_in = &cnt.d[k * n..k * n + n];
            let t_in = &top.d[k * tw..k * tw + n];
            let c_out = &mut cnt2.d[k * w2..k * w2 + w2];
            let t_out = &mut top2.d[k * t2w..k * t2w + w2];
            let full = n / 2; // (pairs both inside the row)
            let m = full.min(w2);
            for g in 0..m {
                let (c0, c1) = (c_in[2 * g], c_in[2 * g + 1]);
                let (t0, t1) = (t_in[2 * g], t_in[2 * g + 1]);
                c_out[g] = if c1 > c0 { c1 } else { c0 };
                t_out[g] = if t1 > t0 { t1 } else { t0 };
            }
            for g in m..w2 {
                c_out[g] = c_in[2 * g];
                t_out[g] = t_in[2 * g];
            }
        }
        return 0;
    }
    for k in 0..cnt.sh[0] {
        for g in 0..w2 {
            let a = 2 * g;
            let mut c = cnt.d[k * n + a];
            let mut t = top.d[k * top.sh[1] + a];
            if a + 1 < n {
                if cnt.d[k * n + a + 1] > c {
                    c = cnt.d[k * n + a + 1];
                }
                if top.d[k * top.sh[1] + a + 1] > t {
                    t = top.d[k * top.sh[1] + a + 1];
                }
            }
            cnt2.d[k * w2 + g] = c;
            top2.d[k * t2w + g] = t;
        }
    }
    0
}

/// Pixel of tick t: floor(t * ax + bx + 0.5) as Numba makes it a whole number.
#[inline(always)]
fn px(t: i64, ax: f64, bx: f64) -> i64 {
    f2i((t as f64 * ax + bx + 0.5).floor())
}

/// midi_index.cover's arguments that stay the same between calls (the tables painted into).
pub struct Paint<'a> {
    pub colour: &'a [i64],
    pub cw: usize,
    pub tab: &'a mut [i64],
    pub nx: &'a mut [i64],
    pub filled: &'a mut [i64],
    pub marks: &'a mut [u8],
    pub nk: i64,
    pub w: i64,
    pub tw: usize,
    pub nxw: usize,
    pub mw: usize,
}

/// midi_index.cover -> where it stopped.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn cover_in(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], st: &mut [i64], cnt_q: &mut [i64],
            first_q: &mut [i64], vel_q: &mut [i64], t1: i64, close_at: i64, ax: f64, bx: f64, k_lo: i64,
            pt: &mut Paint, sq: &mut [i64], sa: &mut [i64], sb: &mut [i64], sv: &mut [i64]) -> i64 {
    let mut p = p;
    let mut tick = st[C_TICK];
    let mut run = st[C_RUN];
    let mut si = st[C_SKIP] as usize;
    let (nk, ww) = (pt.nk, pt.w);
    let room = sq.len();
    let mut n = st[C_SPANS] as usize;
    let byte = |q: i64| b[q as usize] as i64;
    loop {
        while p < end && st[C_STOP] == 0 && n < room {
            while si < skip.len() && skip[si] < base + p {
                si += 1;
            }
            let hid = si < skip.len() && skip[si] == base + p;
            let mut q = p;
            let mut d = 0i64;
            while q < end {
                let c8 = byte(q);
                q += 1;
                d = (d << 7) | (c8 & 127);
                if c8 < 128 {
                    break;
                }
            }
            if q >= end {
                st[C_STOP] = 2;
                break;
            }
            let t = tick + d;
            if t > t1 {
                st[C_STOP] = 1;
                break;
            }
            let mut s = byte(q);
            let explicit = s >= 128;
            if explicit {
                q += 1;
            } else if run == 0 {
                st[C_STOP] = 2;
                break;
            } else {
                s = run;
            }
            if s < 0xF0 {
                let hi = s >> 4;
                let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
                if q + need > end {
                    st[C_STOP] = 2;
                    break;
                }
                if explicit {
                    run = s;
                }
                if (hi == 0x9 || hi == 0x8) && !hid {
                    let k = byte(q);
                    let v = byte(q + 1);
                    let qq = ((s & 15) * 256 + k) as usize;
                    if hi == 0x9 && v > 0 {
                        if cnt_q[qq] == 0 {
                            first_q[qq] = t;
                            vel_q[qq] = v;
                        }
                        cnt_q[qq] += 1;
                        let kk = k - k_lo;
                        let x = px(t, ax, bx);
                        if 0 <= kk && kk < nk && 0 <= x && x < ww {
                            let at = kk as usize * pt.mw + x as usize;
                            pt.marks[at] |= if pt.tab[kk as usize * pt.tw + x as usize] < 0 { 1 } else { 4 };
                        }
                    } else if cnt_q[qq] > 0 {
                        cnt_q[qq] -= 1;
                        if cnt_q[qq] == 0 {
                            sq[n] = qq as i64;
                            sa[n] = first_q[qq];
                            sb[n] = t;
                            sv[n] = vel_q[qq];
                            n += 1;
                        } else {
                            let kk = k - k_lo;
                            let x = px(t, ax, bx);
                            if 0 <= kk && kk < nk && 0 <= x && x < ww {
                                pt.marks[kk as usize * pt.mw + x as usize] |= 4;
                            }
                        }
                    }
                }
                q += need;
            } else if s == 0xFF || s == 0xF0 || s == 0xF7 {
                let mut mt = -1i64;
                if s == 0xFF {
                    if q >= end {
                        st[C_STOP] = 2;
                        break;
                    }
                    mt = byte(q);
                    q += 1;
                }
                let mut ln = 0i64;
                while q < end {
                    let c8 = byte(q);
                    q += 1;
                    ln = (ln << 7) | (c8 & 127);
                    if c8 < 128 {
                        break;
                    }
                }
                if q + ln > end {
                    st[C_STOP] = 2;
                    break;
                }
                q += ln;
                if mt == 0x2F {
                    st[C_STOP] = 2;
                }
            } else {
                st[C_STOP] = 2;
                break;
            }
            tick = t;
            p = q;
        }
        let mut full = n >= room;
        if !full && close_at >= 0 && (p >= end || st[C_STOP] != 0) {
            for qq in 0..cnt_q.len() {
                if cnt_q[qq] > 0 {
                    if n >= room {
                        full = true;
                        break;
                    }
                    sq[n] = qq as i64;
                    sa[n] = first_q[qq];
                    sb[n] = close_at;
                    sv[n] = vel_q[qq];
                    n += 1;
                    cnt_q[qq] = 0;
                }
            }
        }
        for i in 0..n {
            let qq = sq[i];
            let kk = (qq & 255) - k_lo;
            if kk < 0 || kk >= nk {
                continue;
            }
            let mut xa = px(sa[i], ax, bx);
            let mut xb = px(sb[i], ax, bx);
            if xb <= xa {
                xb = xa + 1;
            }
            if xb <= 0 || xa >= ww {
                continue;
            }
            let ends = sb[i] <= t1 && xb <= ww;
            if xa < 0 {
                xa = 0;
            }
            if xb > ww {
                xb = ww;
            }
            let code = pt.colour[(qq >> 8) as usize * pt.cw + (imin(sv[i], 127) / 4) as usize];
            if code < 0 {
                continue;
            }
            let ku = kk as usize;
            let (nxo, tbo, mko) = (ku * pt.nxw, ku * pt.tw, ku * pt.mw);
            let mut x = xa;
            let mut last = -1i64;
            loop {
                let mut r = x;
                while pt.nx[nxo + r as usize] != r {
                    r = pt.nx[nxo + r as usize];
                }
                while pt.nx[nxo + x as usize] != r {
                    let y = pt.nx[nxo + x as usize];
                    pt.nx[nxo + x as usize] = r;
                    x = y;
                }
                x = r;
                if x >= xb {
                    break;
                }
                pt.tab[tbo + x as usize] = code;
                pt.nx[nxo + x as usize] = x + 1;
                pt.filled[ku] += 1;
                last = x;
                x += 1;
            }
            if ends && last == xb - 1 {
                pt.marks[mko + last as usize] |= 2;
            } else if ends {
                pt.marks[mko + (xb - 1) as usize] |= 4;
            }
        }
        n = 0;
        if !full {
            break;
        }
    }
    st[C_TICK] = tick;
    st[C_RUN] = run;
    st[C_SPANS] = 0;
    st[C_SKIP] = si as i64;
    p
}

/// midi_index.cover -> where it stopped.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn cover(b: &[u8], p: i64, end: i64, base: i64, skip: &[i64], st: &mut [i64], cnt_q: &mut [i64],
             first_q: &mut [i64], vel_q: &mut [i64], t1: i64, close_at: i64, ax: f64, bx: f64, w: i64, k_lo: i64,
             colour: Nd<i64, 2>, tab: NdMut<i64, 2>, nx: NdMut<i64, 2>, filled: &mut [i64], marks: NdMut<u8, 2>,
             sq: &mut [i64], sa: &mut [i64], sb: &mut [i64], sv: &mut [i64]) -> i64 {
    let mut pt = Paint { colour: colour.d, cw: colour.sh[1], nk: tab.sh[0] as i64, w, tw: tab.sh[1], nxw: nx.sh[1],
                         mw: marks.sh[1], tab: tab.d, nx: nx.d, filled, marks: marks.d };
    cover_in(b, p, end, base, skip, st, cnt_q, first_q, vel_q, t1, close_at, ax, bx, k_lo, &mut pt, sq, sa, sb, sv)
}

/// midi_index.merge_loud -> 0.
#[inline(always)]
pub fn merge_loud(tab: NdMut<i64, 2>, marks: NdMut<u8, 2>, ttab: NdMut<i64, 2>, tmarks: NdMut<u8, 2>,
                  tnx: NdMut<i64, 2>, tfilled: &mut [i64], ghost: i64) -> i64 {
    let (nk, w) = (tab.sh[0], tab.sh[1]);
    let (mw, tw, tmw, nxw) = (marks.sh[1], ttab.sh[1], tmarks.sh[1], tnx.sh[1]);
    let (tab, marks, ttab, tmarks, tnx) = (tab.d, marks.d, ttab.d, tmarks.d, tnx.d);
    for k in 0..nk {
        if tfilled[k] == 0 {
            continue;
        }
        for x in 0..w {
            let t = ttab[k * tw + x];
            let mt = tmarks[k * tmw + x];
            tmarks[k * tmw + x] = 0;
            if t < 0 {
                continue;
            }
            let g = tab[k * w + x];
            let mi = k * mw + x;
            if g < 0 || (t & 31) + (if t >= ghost { 0 } else { 32 }) > (g & 31) + (if g >= ghost { 0 } else { 32 }) {
                let hid = g >= 0 && marks[mi] != 0;
                tab[k * w + x] = t;
                if mt & 3 != 0 {
                    marks[mi] = mt & 3;
                } else if hid || mt & 4 != 0 {
                    marks[mi] = 4;
                } else {
                    marks[mi] = 0;
                }
            } else if mt != 0 && (marks[mi] & 3) == 0 {
                marks[mi] |= 4;
            }
            ttab[k * tw + x] = -1;
        }
        for x in 0..=w {
            tnx[k * nxw + x] = x as i64;
        }
        tfilled[k] = 0;
    }
    0
}

/// fastloops.midi_cover_many -> how many tracks went into covered.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn cover_many(faint: &[Flag], klo: &[i64], khi: &[i64], starts: &[i64], ends: &[i64], e0s: &[i64], e1s: &[i64],
                  cp_tick: &[i64], cp_run: &[i64], limits: &[i64], lasts: &[i64], buf: &[u8], pos: &[i64],
                  eq: &[i16], ec: &[i64], ev: &[u8], skip: &[i64], t1: i64, ax: f64, bx: f64, w: i64, k_lo: i64,
                  normal: Nd<i64, 2>, behind: Nd<i64, 2>, tab: NdMut<i64, 2>, nx: NdMut<i64, 2>, filled: &mut [i64],
                  marks: NdMut<u8, 2>, sq: &mut [i64], sa: &mut [i64], sb: &mut [i64], sv: &mut [i64],
                  cnt_q: &mut [i64], first_q: &mut [i64], vel_q: &mut [i64], st: &mut [i64], covered: &mut [i64])
                  -> i64 {
    let nk = tab.sh[0] as i64;
    let mut pt = Paint { colour: normal.d, cw: normal.sh[1], nk, w, tw: tab.sh[1], nxw: nx.sh[1], mw: marks.sh[1],
                         tab: tab.d, nx: nx.d, filled, marks: marks.d };
    let mut nc = 0usize;
    for i in 0..starts.len() {
        let a = imax(klo[i], k_lo) - k_lo;
        let b = imin(khi[i], k_lo + nk - 1) - k_lo + 1;
        let mut full = true;
        for r in a..b {
            if pt.filled[r as usize] < w {
                full = false;
                break;
            }
        }
        if full {
            covered[nc] = i as i64;
            nc += 1;
            continue;
        }
        let tick0 = cp_tick[i];
        for e in e0s[i]..e1s[i] {
            let eu = e as usize;
            let q = eq[eu] as i64 as usize;
            cnt_q[q] = ec[eu];
            first_q[q] = tick0;
            vel_q[q] = ev[eu] as i64;
        }
        let size = ends[i] - starts[i];
        let b8 = &buf[pos[i] as usize..(pos[i] + size) as usize];
        st.iter_mut().for_each(|v| *v = 0);
        st[C_TICK] = tick0;
        st[C_RUN] = cp_run[i];
        let colour = if faint[i].on() { &behind } else { &normal };
        pt.colour = colour.d;
        pt.cw = colour.sh[1];
        let p = cover_in(b8, 0, size, starts[i], skip, st, cnt_q, first_q, vel_q, t1, -1, ax, bx, k_lo, &mut pt, sq,
                         sa, sb, sv);
        let close = if st[C_STOP] == 1 || ends[i] < limits[i] { t1 + 1 } else { imax(st[C_TICK], lasts[i]) };
        cover_in(b8, p, p, starts[i], skip, st, cnt_q, first_q, vel_q, t1, close, ax, bx, k_lo, &mut pt, sq, sa, sb,
                 sv);
    }
    nc as i64
}

/// fastloops.loaded_pixels -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn loaded_pixels(img: NdMut<u8, 3>, x0: i64, y0: i64, tab: Nd<i64, 2>, marks: Nd<u8, 2>, c0: i64, c1: i64,
                     kb: i64, row0: &[i64], row1: &[i64], rgb: Nd<u8, 3>) -> i64 {
    let (ih, iw, ic) = (img.sh[0] as i64, img.sh[1] as i64, img.sh[2]);
    let img = img.d;
    let (tw, mw) = (tab.sh[1], marks.sh[1]);
    let (r1, r2) = (rgb.sh[1] * rgb.sh[2], rgb.sh[2]);
    let col = |code: usize, e: usize, i: usize| rgb.d[code * r1 + e * r2 + i];
    for kk in 0..tab.sh[0] {
        let (a, b) = (row0[kk], row1[kk]);
        let (ya, yb) = (imax(a, y0), imin(b, y0 + ih));
        if yb <= ya {
            continue;
        }
        let solid = b - 1 - a < 2;
        for c in c0..c1 {
            let code = tab.d[kk * tw + c as usize];
            if code < 0 {
                continue;
            }
            let x = c + kb - x0;
            if x < 0 || x >= iw {
                continue;
            }
            let mk = marks.d[kk * mw + c as usize];
            let m = (mk & 3) != 0;
            let half = (mk & 4) != 0;
            let cu = code as usize;
            for y in ya..yb {
                let o = (((y - y0) * iw + x) as usize) * ic;
                let e;
                if solid || y == a || y == b - 1 || m {
                    e = 1;
                } else if half {
                    for i in 0..3 {
                        img[o + i] = ((col(cu, 0, i) as i64 + col(cu, 1, i) as i64) / 2) as u8;
                    }
                    continue;
                } else {
                    e = 0;
                }
                img[o] = col(cu, e, 0);
                img[o + 1] = col(cu, e, 1);
                img[o + 2] = col(cu, e, 2);
            }
        }
    }
    0
}

/// fastloops.all_tops -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn all_tops(top: NdMut<i32, 2>, data: &[u8], offs: &[i64], nrows: &[i64], nbytes: &[i64], pklo: &[i64],
                glo: &[i64], pf: &[i64], cols: &[i64], used: &[Flag], uoff: &[i64], ids: &[i64], x0: i64, tpp: f64,
                g_: i64) -> i64 {
    let (span, w) = (top.sh[0] as i64, top.sh[1]);
    let top = top.d;
    let mut c0 = vec![0i64; w + 1];
    let mut c1 = vec![0i64; w + 1];
    for &r in ids {
        let r = r as usize;
        let n = cols[r];
        if n == 0 || nrows[r] == 0 {
            continue;
        }
        let step = g_ * pf[r];
        let base = glo[r] * g_;
        let (stf, bf) = (step as f64, base as f64);
        if f2i(((x0 as f64 * tpp - bf) / stf).floor()) >= n
            || f2i((((x0 + w as i64) as f64 * tpp + tpp - bf) / stf).ceil()) <= 0 {
            continue;
        }
        let (mut first, mut last) = (-1i64, -1i64);
        for x in 0..=w {
            let ta = (x0 + x as i64) as f64 * tpp;
            let a = f2i(((ta - bf) / stf).floor());
            let b = imax(f2i(((ta + tpp - bf) / stf).ceil()), a + 1);
            c0[x] = a;
            c1[x] = b;
            if b > 0 && a < n {
                if first < 0 {
                    first = x as i64;
                }
                last = x as i64;
            }
        }
        if first < 0 {
            continue;
        }
        let joined = tpp >= stf;
        let (o, nb, k0) = (offs[r], nbytes[r], pklo[r]);
        for x in first..imin(last + 1, w as i64) {
            let xu = x as usize;
            let a = imin(imax(c0[xu], 0), n - 1);
            let e = if !joined {
                a + 1
            } else if x < last {
                imax(imin(imax(c0[xu + 1], 0), n - 1), a + 1)
            } else {
                imax(imin(imax(c1[last as usize], 1), n), a + 1)
            };
            let u = uoff[r];
            let mut hit = false;
            for c in a..e {
                if used[(u + c) as usize].on() {
                    hit = true;
                    break;
                }
            }
            if !hit {
                continue;
            }
            for row in 0..nrows[r] {
                let key = k0 + row;
                if key >= span {
                    break;
                }
                let rb = o + row * nb;
                for c in a..e {
                    if (data[(rb + (c >> 3)) as usize] >> (7 - (c & 7))) & 1 != 0 {
                        top[key as usize * w + xu] = r as i32;
                        break;
                    }
                }
            }
        }
    }
    0
}
