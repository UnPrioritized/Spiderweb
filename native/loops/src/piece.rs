//! Rendered ahead's pieces of a loaded MIDI (files/midi_piece.py, compiled there by fastloops as piece_track,
//! piece_held_ons, piece_held_head, piece_note_ons, piece_runs_sort, piece_runs_merge, piece_counted_track,
//! piece_order_ok, piece_open_at): the very same steps.
use crate::maths::fmodi;
use crate::pyext::{Out, Outs};

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

/// A wait dd written as a variable-length number at out[w..] (as midi_piece: the bytes it needs, high first).
#[inline(always)]
fn put_wait(out: &mut [u8], mut w: usize, dd: i64) -> usize {
    let mut m = 0;
    let mut tmp = dd;
    loop {
        m += 1;
        tmp >>= 7;
        if tmp == 0 {
            break;
        }
    }
    for i in (0..m).rev() {
        out[w] = (((dd >> (7 * i)) & 127) | if i > 0 { 0x80 } else { 0 }) as u8;
        w += 1;
    }
    w
}

/// The next variable-length number from q (stops at the bytes' end): (value, q).
#[inline(always)]
fn varlen(b: &[u8], mut q: usize) -> (i64, usize) {
    let n = b.len();
    let mut d = 0i64;
    while q < n {
        let c = b[q] as i64;
        q += 1;
        d = (d << 7) | (c & 127);
        if c < 128 {
            break;
        }
    }
    (d, q)
}

/// midi_piece.piece_track -> (bytes written, last, bad).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn piece_track(b: &[u8], base: i64, tick: i64, run: i64, t_pre: i64, t_end: i64, sk: &[i64], cnt: &mut [i64],
                   vel: &mut [i64], ontk: &mut [i64], muted: i64, newch: i64, last: i64, out: &mut [u8], t_on: i64,
                   opn: &mut [i64], port: i64, base_t: i64, bars: &[i64], bar_ch: &[u8], ev_key: &mut [i64],
                   ev_sig: &mut [u8], nev: &mut [i64]) -> Outs {
    let (w, last, bad) = piece_walk(b, base, tick, run, t_pre, t_end, sk, cnt, vel, ontk, muted, newch, last, out,
                                    t_on, opn, port, base_t, bars, bar_ch, ev_key, ev_sig, nev);
    Ok(vec![Out::Int(w as i64), Out::Int(last), Out::Int(bad)])
}

#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn piece_walk(b: &[u8], base: i64, tick: i64, run: i64, t_pre: i64, t_end: i64, sk: &[i64], cnt: &mut [i64],
              vel: &mut [i64], ontk: &mut [i64], muted: i64, newch: i64, last: i64, out: &mut [u8], t_on: i64,
              opn: &mut [i64], port: i64, base_t: i64, bars: &[i64], bar_ch: &[u8], ev_key: &mut [i64],
              ev_sig: &mut [u8], nev: &mut [i64]) -> (usize, i64, i64) {
    let n = b.len();
    let (mut tick, mut run, mut last) = (tick, run, last);
    let mut p = 0usize;
    let mut w = 0usize;
    let mut si = 0usize;
    let nsk = sk.len();
    let counted = !ev_key.is_empty();
    let nb = bars.len();
    let mut copying = false;
    let mut nopen: i64 = 0;
    let mut wrote: i64 = -1;
    while p < n {
        let pos = base + p as i64;
        while si < nsk && sk[si] < pos {
            si += 1;
        }
        let hid = si < nsk && sk[si] == pos;
        let (d, mut q) = varlen(b, p);
        let t = tick + d;
        if t > t_end {
            return (w, last, 0);
        }
        if q >= n {
            return (w, last, 1);
        }
        if !copying && t >= t_pre {
            copying = true;
            nopen = 0;
            for i in 0..cnt.len() {
                opn[i] = cnt[i];
                nopen += cnt[i];
            }
        }
        if copying && t > t_on && nopen <= 0 {
            return (w, last, 0);
        }
        let mut s = b[q] as i64;
        if s >= 128 {
            q += 1;
        } else if run == 0 {
            return (w, last, 1);
        } else {
            s = run;
        }
        if s < 0xF0 {
            let hi = s >> 4;
            let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
            if q + need > n {
                return (w, last, 1);
            }
            run = s;
            let ch = s & 15;
            let mut skip = hid || (muted >> ch) & 1 == 1;
            if (hi == 0x9 || hi == 0x8 || hi == 0xA) && (b[q] as i64) > 127 {
                skip = true;
            }
            if copying && !skip {
                let is_on = hi == 0x9 && (b[q + 1] as i64) > 0;
                if is_on || hi == 0x8 || hi == 0x9 {
                    let k = u(ch * 256 + b[q] as i64);
                    if t > t_on && (is_on || opn[k] <= 0) {
                        skip = true;
                    } else if is_on {
                        opn[k] += 1;
                        nopen += 1;
                    } else if opn[k] > 0 {
                        opn[k] -= 1;
                        nopen -= 1;
                    }
                } else if t > t_on {
                    skip = true;
                }
                if counted && !skip && (hi == 0x9 || hi == 0x8) {
                    let och = if newch >= 0 { newch } else { ch };
                    let pc = port * 16 + och;
                    let mut stay = false;
                    if bar_ch[u(pc)] & 1 != 0 {
                        let bk = (t << 8) | pc;
                        let (mut lo, mut up) = (0usize, nb);
                        while lo < up {
                            let mid = (lo + up) >> 1;
                            if bars[mid] < bk {
                                lo = mid + 1;
                            } else {
                                up = mid;
                            }
                        }
                        stay = lo < nb && bars[lo] == bk;
                    }
                    if !stay {
                        let e = u(nev[0]);
                        ev_key[e] = ((t - base_t) << 16) | (pc << 8) | ((b[q] as i64) << 1)
                            | if is_on && bar_ch[u(pc)] & 2 == 0 { 1 } else { 0 };
                        ev_sig[e] = if is_on { b[q + 1] } else { 0 };
                        nev[0] = e as i64 + 1;
                        skip = true;
                    }
                }
            }
            if !copying {
                if !skip && (hi == 0x9 || hi == 0x8) {
                    let k = u(ch * 256 + b[q] as i64);
                    let v = b[q + 1] as i64;
                    if hi == 0x9 && v > 0 {
                        cnt[k] += 1;
                        vel[k] = v;
                        ontk[k] = t;
                    } else if cnt[k] > 0 {
                        cnt[k] -= 1;
                    }
                }
            } else if !skip {
                if newch >= 0 {
                    s = (s & 0xF0) | newch;
                }
                w = put_wait(out, w, t - last);
                if s != wrote {
                    out[w] = s as u8;
                    w += 1;
                    wrote = s;
                }
                for i in 0..need {
                    out[w] = b[q + i];
                    w += 1;
                }
                last = t;
            }
            q += need;
        } else if s == 0xFF || s == 0xF0 || s == 0xF7 {
            let start = q;
            let mut mt: i64 = -1;
            if s == 0xFF {
                if q >= n {
                    return (w, last, 1);
                }
                mt = b[q] as i64;
                q += 1;
            }
            let (ln, q2) = varlen(b, q);
            q = q2;
            if q as i64 + ln > n as i64 {
                return (w, last, 1);
            }
            if mt == 0x2F {
                return (w, last, 0);
            }
            let ln = ln as usize;
            if copying && !hid && t <= t_on {
                if counted && s != 0xFF {
                    nev[1] = 1;
                }
                w = put_wait(out, w, t - last);
                out[w] = s as u8;
                w += 1;
                for i in start..q + ln {
                    out[w] = b[i];
                    w += 1;
                }
                if mt == 0x51 || mt == 0x21 {
                    out[w - (q + ln - start)] = 0x01;
                }
                wrote = -1;
                last = t;
            }
            q += ln;
        } else {
            return (w, last, 1);
        }
        tick = t;
        p = q;
    }
    (w, last, 0)
}

/// One event's walk shared by held_ons / note_ons: what came of the step.
enum Step {
    Next(usize, i64, i64),
    On(usize, i64, i64, i64, i64),
    Stop(i64),
}

/// The step at p of held_ons / note_ons (they walk alike): Stop(code) where they return (`end_ok` = the code of a
/// plain end: past t_stop, end of the bytes, End of Track; `bad` = the code of broken bytes), On(next p, tick, run,
/// channel, key place's key + velocity packed) for a note-on to look at, else Next(next p, tick, run).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn ons_step(b: &[u8], p: usize, tick: i64, run: i64, t_stop: i64, hid: bool, muted: i64, end_ok: i64, bad: i64)
            -> Step {
    let n = b.len();
    let (d, mut q) = varlen(b, p);
    let t = tick + d;
    if t >= t_stop || q >= n {
        return Step::Stop(end_ok);
    }
    let mut s = b[q] as i64;
    if s >= 128 {
        q += 1;
    } else if run == 0 {
        return Step::Stop(bad);
    } else {
        s = run;
    }
    if s < 0xF0 {
        let hi = s >> 4;
        let need = if hi == 0xC || hi == 0xD { 1 } else { 2 };
        if q + need > n {
            return Step::Stop(bad);
        }
        let ch = s & 15;
        if hi == 0x9 && !hid && (muted >> ch) & 1 == 0 {
            let key = b[q] as i64;
            let v = b[q + 1] as i64;
            return Step::On(q + need, t, s, ch * 256 + key, (key << 8) | v);
        }
        Step::Next(q + need, t, s)
    } else if s == 0xFF || s == 0xF0 || s == 0xF7 {
        let mut mt: i64 = -1;
        if s == 0xFF {
            if q >= n {
                return Step::Stop(bad);
            }
            mt = b[q] as i64;
            q += 1;
        }
        let (ln, q2) = varlen(b, q);
        q = q2;
        if q as i64 + ln > n as i64 {
            return Step::Stop(bad);
        }
        if mt == 0x2F {
            return Step::Stop(end_ok);
        }
        Step::Next(q + ln as usize, t, run)
    } else {
        Step::Stop(bad)
    }
}

/// midi_piece.held_ons -> 1 when the bytes break.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn held_ons(b: &[u8], base: i64, tick: i64, run: i64, t_stop: i64, sk: &[i64], muted: i64, need: &[i64],
                off: &[i64], tk: &mut [i64], vl: &mut [i64], pos: &mut [i64]) -> i64 {
    let n = b.len();
    let (mut p, mut si, mut tick, mut run) = (0usize, 0usize, tick, run);
    let nsk = sk.len();
    while p < n {
        let pos_b = base + p as i64;
        while si < nsk && sk[si] < pos_b {
            si += 1;
        }
        let hid = si < nsk && sk[si] == pos_b;
        match ons_step(b, p, tick, run, t_stop, hid, muted, 0, 1) {
            Step::Stop(c) => return c,
            Step::Next(q, t, r) => {
                p = q;
                tick = t;
                run = r;
            }
            Step::On(q, t, r, k, kv) => {
                let (key, v) = (kv >> 8, kv & 255);
                let ku = u(k);
                if key < 128 && v > 0 && need[ku] > 0 {
                    let i = u(off[ku] + fmodi(pos[ku], need[ku]));
                    tk[i] = t;
                    vl[i] = v;
                    pos[ku] += 1;
                }
                p = q;
                tick = t;
                run = r;
            }
        }
    }
    0
}

/// midi_piece.note_ons -> how many.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn note_ons(b: &[u8], base: i64, tick: i64, run: i64, t_stop: i64, sk: &[i64], muted: i64, want: &[i8],
                out_t: &mut [i64], out_q: &mut [i64], out_v: &mut [i64]) -> i64 {
    let n = b.len();
    let (mut p, mut si, mut tick, mut run) = (0usize, 0usize, tick, run);
    let nsk = sk.len();
    let mut got: usize = 0;
    while p < n {
        let pos_b = base + p as i64;
        while si < nsk && sk[si] < pos_b {
            si += 1;
        }
        let hid = si < nsk && sk[si] == pos_b;
        match ons_step(b, p, tick, run, t_stop, hid, muted, -1, -1) {
            Step::Stop(_) => return got as i64,
            Step::Next(q, t, r) => {
                p = q;
                tick = t;
                run = r;
            }
            Step::On(q, t, r, k, kv) => {
                let (key, v) = (kv >> 8, kv & 255);
                if key < 128 && v > 0 && want[u(k)] != 0 && got < out_t.len() {
                    out_t[got] = t;
                    out_q[got] = k;
                    out_v[got] = v;
                    got += 1;
                }
                p = q;
                tick = t;
                run = r;
            }
        }
    }
    got as i64
}

/// midi_piece.held_head -> (bytes written, last).
#[inline(always)]
pub fn held_head(tk: &[i64], qs: &[i64], vl: &[i64], newch: i64, last: i64, out: &mut [u8]) -> Outs {
    let mut w = 0usize;
    let mut last = last;
    for i in 0..tk.len() {
        w = put_wait(out, w, tk[i] - last);
        let ch = if newch >= 0 { newch } else { qs[i] >> 8 };
        out[w] = (0x90 | ch) as u8;
        out[w + 1] = (qs[i] & 255) as u8;
        out[w + 2] = (if vl[i] > 1 { vl[i] } else { 1 }) as u8;
        w += 3;
        last = tk[i];
    }
    Ok(vec![Out::Int(w as i64), Out::Int(last)])
}

/// midi_piece.runs_sort_fold -> how many are left.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn runs_sort(key: &mut [i64], sig: &mut [u8], cnt: &mut [i64], n: i64, tk: &mut [i64], ts: &mut [u8],
                 tc: &mut [i64], bits: i64) -> i64 {
    let nu = n.max(0) as usize;
    if [key.len(), sig.len(), cnt.len(), tk.len(), ts.len(), tc.len()].iter().any(|&l| l < nu) {
        return crate::FAILED; // (arrays shorter than n: Numba's loop reads past them, it runs instead)
    }
    let mut shift = 0i64;
    let mut in_main = true;
    let mut hist = vec![0i64; 16385];
    while shift < bits {
        {
            let (kk, ss, cc, dk, ds, dc): (&[i64], &[u8], &[i64], &mut [i64], &mut [u8], &mut [i64]) = if in_main {
                (&*key, &*sig, &*cnt, &mut *tk, &mut *ts, &mut *tc)
            } else {
                (&*tk, &*ts, &*tc, &mut *key, &mut *sig, &mut *cnt)
            };
            hist.iter_mut().for_each(|h| *h = 0);
            for i in 0..nu {
                let d = u(((g(kk, i) >> shift) & 16383) + 1); // (masked: always inside hist)
                { let v = g(&hist, d) + 1; put(&mut hist, d, v); }
            }
            for d in 0..16384 {
                hist[d + 1] += hist[d];
            }
            // (a counting sort: every place o is below nu, each taken once)
            for i in 0..nu {
                let d = u((g(kk, i) >> shift) & 16383);
                let o = u(g(&hist, d));
                put(&mut hist, d, o as i64 + 1);
                put(dk, o, g(kk, i));
                put(ds, o, g(ss, i));
                put(dc, o, g(cc, i));
            }
        }
        in_main = !in_main;
        shift += 14;
    }
    if !in_main {
        for i in 0..nu {
            key[i] = tk[i];
            sig[i] = ts[i];
            cnt[i] = tc[i];
        }
    }
    let mut m = 0usize;
    for i in 0..nu {
        if m > 0 && key[m - 1] == key[i] && sig[m - 1] == sig[i] {
            cnt[m - 1] += cnt[i];
        } else {
            key[m] = key[i];
            sig[m] = sig[i];
            cnt[m] = cnt[i];
            m += 1;
        }
    }
    m as i64
}

/// midi_piece.runs_merge -> how many.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn runs_merge(ak: &[i64], asg: &[u8], ac: &[i64], na: i64, bk: &[i64], bsg: &[u8], bc: &[i64], nb: i64,
                  ok: &mut [i64], osg: &mut [u8], oc: &mut [i64]) -> i64 {
    let (na, nb) = (na.max(0), nb.max(0));
    let (au, bu) = (na as usize, nb as usize);
    if ak.len().min(asg.len()).min(ac.len()) < au || bk.len().min(bsg.len()).min(bc.len()) < bu
        || ok.len().min(osg.len()).min(oc.len()) < au + bu {
        return crate::FAILED; // (arrays shorter than said: Numba's loop runs instead)
    }
    let (mut i, mut j, mut m) = (0usize, 0usize, 0usize);
    while i < au || j < bu {
        let (k, s, c);
        if j >= bu || (i < au && g(ak, i) <= g(bk, j)) {
            k = g(ak, i);
            s = g(asg, i);
            c = g(ac, i);
            i += 1;
        } else {
            k = g(bk, j);
            s = g(bsg, j);
            c = g(bc, j);
            j += 1;
        }
        if m > 0 && g(ok, m - 1) == k && g(osg, m - 1) == s {
            put(oc, m - 1, g(oc, m - 1) + c);
        } else {
            put(ok, m, k);
            put(osg, m, s);
            put(oc, m, c);
            m += 1;
        }
    }
    m as i64
}

/// midi_piece.order_ok -> 1 (True) / 0 (False).
#[inline(always)]
pub fn order_ok(key: &[i64], sig: &[u8], cnt: &[i64], n: i64, bar_ch: &[u8]) -> i64 {
    let Some(mut sounding) = crate::try_zeros::<i64>(1 << 15) else {
        return crate::NO_MEMORY;
    };
    for i in 0..n.max(0) as usize {
        let place = u((key[i] >> 1) & 32767);
        if sig[i] > 0 {
            sounding[place] += cnt[i];
        } else {
            if bar_ch[place >> 7] & 2 == 0 && cnt[i] > sounding[place] {
                return 0;
            }
            let left = sounding[place] - cnt[i];
            sounding[place] = if left > 0 { left } else { 0 };
        }
    }
    1
}

/// midi_piece.open_at -> the rows taken.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn open_at(key: &[i64], sig: &[u8], cnt: &[i64], n: i64, lim: i64, wlim: i64, take: &mut [i64]) -> i64 {
    let (Some(mut opn), Some(mut gone)) = (crate::try_zeros::<i64>(1 << 15), crate::try_zeros::<i64>(1 << 15)) else {
        return crate::NO_MEMORY;
    };
    let nu = n.max(0) as usize;
    if key.len().min(sig.len()).min(cnt.len()).min(take.len()) < nu {
        return crate::FAILED; // (arrays shorter than n: Numba's loop runs instead)
    }
    let mut m = 0usize;
    while m < nu && g(key, m) < lim {
        m += 1;
    }
    // (places are masked to 15 bits: always inside opn / gone)
    for i in 0..m {
        let place = u((g(key, i) >> 1) & 32767);
        let c = g(cnt, i);
        if g(sig, i) > 0 {
            { let v = g(&opn, place) + c; put(&mut opn, place, v); }
        } else {
            let o = g(&opn, place);
            let k = if o < c { o } else { c };
            put(&mut opn, place, o - k);
            if g(key, i) < wlim {
                { let v = g(&gone, place) + k; put(&mut gone, place, v); }
            }
        }
    }
    let mut rows = 0i64;
    for i in 0..m {
        let place = u((g(key, i) >> 1) & 32767);
        let c = g(cnt, i);
        let mut tk = 0;
        if g(sig, i) > 0 {
            let gp = g(&gone, place);
            let k = if c < gp { c } else { gp };
            put(&mut gone, place, gp - k);
            tk = c - k;
        } else if g(key, i) >= wlim {
            tk = c;
        }
        put(take, i, tk);
        if tk > 0 {
            rows += 1;
        }
    }
    rows
}

/// midi_piece.counted_track -> (bytes written, last tick written).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn counted_track(key: &[i64], sig: &[u8], cnt: &[i64], n: i64, port: i64, rel: i64, out: &mut [u8]) -> Outs {
    let mut w = 0usize;
    let mut last = 0i64;
    for i in 0..n.max(0) as usize {
        let k = key[i];
        if (k >> 12) & 15 != port {
            continue;
        }
        let t = (k >> 16) + rel;
        let ch = (k >> 8) & 15;
        let note = ((k >> 1) & 127) as u8;
        let v = sig[i];
        let c = cnt[i];
        let mut times = c;
        let mut dd = t - last;
        if c >= 5 {
            times = 1;
            w = put_wait(out, w, dd);
            out[w..w + 11].copy_from_slice(&[0xFF, 0x7F, 8, 83, 87, 110, note, ((c >> 24) & 255) as u8,
                                             ((c >> 16) & 255) as u8, ((c >> 8) & 255) as u8, (c & 255) as u8]);
            w += 11;
            dd = 0;
        }
        for _ in 0..times.max(0) {
            w = put_wait(out, w, dd);
            out[w] = ((if v > 0 { 0x90 } else { 0x80 }) | ch) as u8;
            out[w + 1] = note;
            out[w + 2] = v;
            w += 3;
            dd = 0;
        }
        last = t;
    }
    Ok(vec![Out::Int(w as i64), Out::Int(last)])
}
