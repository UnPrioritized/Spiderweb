//! MIDI bytes, Image to notes, custom shapes' areas, Nested fill: fastloops' midi_events, midi_track, nearest,
//! spread_line, area_spots, smooth_sor. The same sums in the same order as those.
use std::cell::RefCell;

use crate::maths::{fdiv, pmax, pmin};
use crate::{gathered, need, try_zeros, zeros, Nd, NdMut, NO_MEMORY};

thread_local! {
    /// Bytes whose length isn't known before the loop runs (midi_events / midi_track): kept here until take_bytes
    /// copies them out (the same thread asks right after).
    static BYTES: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
}

/// The stashed bytes into out (its length as the loop said) -> 0.
#[inline(always)]
pub fn take_bytes(out: &mut [u8]) -> i64 {
    BYTES.with(|b| {
        let mut b = b.borrow_mut();
        out.copy_from_slice(&b[..out.len()]);
        *b = Vec::new();
    });
    0
}

/// fastloops._put_vlq: a MIDI wait at out[p] -> the next p.
#[inline(always)]
fn put_vlq(out: &mut [u8], p: usize, d: i64) -> usize {
    let k = 1 + (d >= 1 << 7) as usize + (d >= 1 << 14) as usize + (d >= 1 << 21) as usize;
    for b in 0..k {
        let left = k - 1 - b;
        out[p + b] = (((d >> (7 * left)) & 0x7F) | if left > 0 { 0x80 } else { 0 }) as u8;
    }
    p + k
}

/// fastloops.midi_events' bytes (note = row rows[i]: (start, end, key, velocity) of nt's nc columns) -> Vec.
#[inline(always)]
fn events(nt: &[i64], nc: usize, rows: &[i64], order: &[i64], ch: i64, most: i64) -> Option<Vec<u8>> {
    let at = |r: i64, c: usize| nt[r as usize * nc + c];
    let (mut size, mut last) = (0usize, 0i64);
    for &j in order {
        let mut d = at(rows[(j >> 1) as usize], (j & 1) as usize) - last;
        last += d;
        while d > most {
            size += 7;
            d -= most;
        }
        size += 4 + (d >= 1 << 7) as usize + (d >= 1 << 14) as usize + (d >= 1 << 21) as usize;
    }
    let mut out: Vec<u8> = try_zeros(size)?;
    let (mut p, mut last) = (0usize, 0i64);
    for &j in order {
        let r = rows[(j >> 1) as usize];
        let mut d = at(r, (j & 1) as usize) - last;
        last += d;
        while d > most {
            p = put_vlq(&mut out, p, most);
            out[p] = 0xFF;
            out[p + 1] = 0x01;
            out[p + 2] = 0x00;
            p += 3;
            d -= most;
        }
        p = put_vlq(&mut out, p, d);
        let on = (j & 1) == 0;
        out[p] = ((if on { 0x90 } else { 0x80 }) | ch) as u8;
        out[p + 1] = (at(r, 2) & 0xFF) as u8;
        out[p + 2] = if on { (at(r, 3) & 0xFF) as u8 } else { 0 };
        p += 3;
    }
    Some(out)
}

fn stash(v: Vec<u8>) -> i64 {
    let n = v.len() as i64;
    BYTES.with(|b| *b.borrow_mut() = v);
    n
}

/// fastloops.midi_events: the bytes stashed (take_bytes) -> how many.
#[inline(always)]
pub fn midi_events(notes: Nd<i64, 2>, rows: &[i64], order: &[i64], ch: i64, most: i64) -> i64 {
    match events(notes.d, notes.sh[1], rows, order, ch, most) {
        Some(v) => stash(v),
        None => NO_MEMORY,
    }
}

/// fastloops.radix_order on a copy (k changed as work space there; here nobody sees it) -> the order.
#[inline(always)]
fn radix(mut k1: Vec<i64>, bits: i64) -> Result<Vec<i64>, i64> {
    let n = k1.len();
    let mut order: Vec<i64> = gathered((0..n).map(|i| i as i64))?;
    let mut o2: Vec<i64> = zeros(n)?;
    let mut k2: Vec<i64> = zeros(n)?;
    let mut count = [0i64; 2049];
    let mut shift = 0i64;
    while shift < bits && n > 1 {
        count.iter_mut().for_each(|c| *c = 0);
        for &v in &k1 {
            count[((v >> shift) & 2047) as usize + 1] += 1;
        }
        if count[((k1[0] >> shift) & 2047) as usize + 1] == n as i64 {
            shift += 11;
            continue;
        }
        for d in 0..2048 {
            count[d + 1] += count[d];
        }
        for i in 0..n {
            let d = ((k1[i] >> shift) & 2047) as usize;
            let p = count[d] as usize;
            o2[p] = order[i];
            k2[p] = k1[i];
            count[d] = p as i64 + 1;
        }
        std::mem::swap(&mut order, &mut o2);
        std::mem::swap(&mut k1, &mut k2);
        shift += 11;
    }
    Ok(order)
}

/// fastloops.midi_track: the bytes stashed (take_bytes) -> how many.
#[inline(always)]
pub fn midi_track(notes: Nd<i64, 2>, rows: &[i64], ch: i64, most: i64, bits: i64) -> i64 {
    let m = rows.len();
    let nc = notes.sh[1];
    let mut own: Vec<i64> = need!(zeros(m * 4));
    let mut k: Vec<i64> = need!(zeros(2 * m));
    for i in 0..m {
        let r = rows[i] as usize;
        own[i * 4..i * 4 + 4].copy_from_slice(&notes.d[r * nc..r * nc + 4]);
        k[2 * i] = own[i * 4] * 2 + 1;
        k[2 * i + 1] = own[i * 4 + 1] * 2;
    }
    let order = need!(radix(k, bits));
    let rows2: Vec<i64> = need!(gathered((0..m).map(|i| i as i64)));
    match events(&own, 4, &rows2, &order, ch, most) {
        Some(v) => stash(v),
        None => NO_MEMORY,
    }
}

/// The palette colour nearest one colour (squared distance summed channel by channel; the first of equal ones, or
/// the first not-a-number, as argmin).
#[inline(always)]
fn nearest_one(c: &[f64], pal: &[f64], np: usize) -> i64 {
    let (mut best, mut bd) = (0i64, 0.0f64);
    for j in 0..np {
        let (d0, d1, d2) = (c[0] - pal[j * 3], c[1] - pal[j * 3 + 1], c[2] - pal[j * 3 + 2]);
        let mut d = d0 * d0;
        d += d1 * d1;
        d += d2 * d2;
        if d != d {
            best = j as i64;
            break;
        }
        if j == 0 || d < bd {
            best = j as i64;
            bd = d;
        }
    }
    best
}

/// fastloops.nearest into out -> 0.
#[inline(always)]
pub fn nearest(lab: Nd<f64, 2>, cen: Nd<f64, 2>, out: &mut [i64]) -> i64 {
    if lab.sh[1] != 3 || cen.sh[1] != 3 {
        return crate::FAILED;
    }
    let n = lab.sh[0];
    for (i, o) in out[..n].iter_mut().enumerate() {
        *o = nearest_one(&lab.d[i * 3..i * 3 + 3], cen.d, cen.sh[0]);
    }
    0
}

/// fastloops.spread_line -> how many cells line `line` has.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn spread_line(a0: Nd<f64, 3>, free: Nd<f64, 2>, pal: Nd<f64, 2>, pal_lab: Nd<f64, 2>, strength: f64,
                   idx: NdMut<i64, 2>, err: NdMut<f64, 3>, line: i64, lab: Nd<f64, 2>, vals: NdMut<f64, 2>,
                   clipped: NdMut<f64, 2>) -> i64 {
    let (rows, cols) = (idx.sh[0] as i64, idx.sh[1] as i64);
    let (idx, err, vals, clipped) = (idx.d, err.d, vals.d, clipped.d);
    let cu = cols as usize;
    let fr = |y: i64, x: i64| free.d[y as usize * cu + x as usize];
    let ei = |y: i64, x: i64, c: usize| (y as usize * cu + x as usize) * 3 + c;
    let (vc, cc) = (3usize, 3usize);
    if pal.sh[1] != 3 || pal_lab.sh[1] != 3 || lab.sh[1] != 3 || a0.sh[2] != 3 {
        return crate::FAILED;
    }
    if line > 0 {
        let y0 = 0i64.max(fdiv(line - 1 - cols + 2, 2));
        for n in 0..lab.sh[0] {
            let y = y0 + n as i64;
            let x = line - 1 - 2 * y;
            let best = nearest_one(&lab.d[n * 3..n * 3 + 3], pal_lab.d, pal_lab.sh[0]);
            idx[y as usize * cu + x as usize] = best;
            let fs = strength * fr(y, x);
            for c in 0..3 {
                err[ei(y, x, c)] = (vals[n * vc + c] - pal.d[best as usize * 3 + c]) * fs;
            }
        }
    }
    let (y0, y1) = (0i64.max(fdiv(line - cols + 2, 2)), (rows - 1).min(fdiv(line, 2)));
    let count = 0i64.max(y1 - y0 + 1);
    for n in 0..count {
        let y = y0 + n;
        let x = line - 2 * y;
        let ft = fr(y, x);
        for c in 0..3 {
            let mut t = a0.d[ei(y, x, c)];
            if y > 0 && x > 0 {
                t += err[ei(y - 1, x - 1, c)] * (1.0 / 16.0) * ft;
            }
            if y > 0 {
                t += err[ei(y - 1, x, c)] * (5.0 / 16.0) * ft;
            }
            if x > 0 {
                t += err[ei(y, x - 1, c)] * (7.0 / 16.0) * ft;
            }
            if y > 0 && x + 1 < cols {
                t += err[ei(y - 1, x + 1, c)] * (3.0 / 16.0) * ft;
            }
            vals[n as usize * vc + c] = t;
            clipped[n as usize * cc + c] = pmin(pmax(t, 0.0), 1.0);
        }
    }
    count
}

/// fastloops.area_spots into out -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn area_spots(x: &[f64], y: &[f64], levels: &[f64], start: &[i64], off: &[i64], lab: &[i64], outside: i64,
                  edge: &[i64], ex: &[f64], ey: &[f64], es: &[f64], x0: &[f64], y0: &[f64], x1: &[f64], y1: &[f64],
                  eps: f64, out: &mut [i64]) -> i64 {
    let nl = levels.len() as i64;
    let at_x = |e: usize, yi: f64| x0[e] + (x1[e] - x0[e]) * (yi - y0[e]) / (y1[e] - y0[e]);
    for i in 0..x.len() {
        let (xi, yi) = (x[i], y[i]);
        let (mut a, mut b) = (0i64, nl);
        while a < b {
            let m = (a + b) >> 1;
            if levels[m as usize] <= yi {
                a = m + 1;
            } else {
                b = m;
            }
        }
        let s = a - 1;
        if s < 0 || s >= nl - 1 {
            out[i] = lab[outside as usize];
            continue;
        }
        let su = s as usize;
        let (first, end) = (start[su], start[su + 1]);
        let (mut lo, mut hi) = (first, end);
        while lo < hi {
            let mid = ((lo + hi) >> 1) as usize;
            if ex[mid] + es[mid] * (yi - ey[mid]) < xi {
                lo = mid as i64 + 1;
            } else {
                hi = mid as i64;
            }
        }
        let margin = eps * (end - first + 1) as f64;
        let mut near = false;
        for side in [lo - 1, lo] {
            if first <= side && side < end {
                let e = edge[side as usize] as usize;
                if (at_x(e, yi) - xi).abs() <= margin {
                    near = true;
                }
            }
        }
        if near {
            lo = first;
            for j in first..end {
                let e = edge[j as usize] as usize;
                if at_x(e, yi) < xi {
                    lo += 1;
                }
            }
        }
        out[i] = lab[(off[su] + lo - first) as usize];
    }
    0
}

/// synth.dc_block (fastloops.dc_block): x (frames, 2) float32 high-passed in place, z (2, 2) its state -> 0.
#[inline(always)]
pub fn dc_block(x: NdMut<f32, 2>, c: &[f64], z: NdMut<f64, 2>) -> i64 {
    if x.sh[1] != 2 || z.sh[0] != 2 || z.sh[1] != 2 {
        return crate::FAILED;
    }
    let (b0, b1, b2, a1, a2) = (c[0], c[1], c[2], c[3], c[4]);
    let (x, z) = (x.d, z.d);
    for i in 0..x.len() / 2 {
        for ch in 0..2 {
            let s = x[i * 2 + ch] as f64;
            let y = b0 * s + z[ch * 2];
            z[ch * 2] = b1 * s - a1 * y + z[ch * 2 + 1];
            z[ch * 2 + 1] = b2 * s - a2 * y;
            x[i * 2 + ch] = y as f32;
        }
    }
    0
}

/// fastloops.smooth_sor (flat changed in place) -> 0.
#[inline(always)]
pub fn smooth_sor(flat: &mut [f64], red: &[i64], black: &[i64], wide: i64, om: f64, times: i64) -> i64 {
    let w = wide as usize;
    for _ in 0..times {
        for part in [red, black] {
            for &i in part {
                let i = i as usize;
                let g = (flat[i - 1] + flat[i + 1] + flat[i - w] + flat[i + w]) / 4.0;
                let here = flat[i];
                flat[i] = here + om * (g - here);
            }
        }
    }
    0
}
