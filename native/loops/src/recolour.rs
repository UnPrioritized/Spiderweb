//! Recolour / Stir / Liquify's helpers (notes/recolour.py, liquify.py): fastloops' stir_nearest, stir_wrap,
//! liquify_sample (each spot on its own: shared out over the pool past LEAST spots, the very same numbers),
//! dice_mix, recolour_apart. The same sums in the same order as those.
use rayon::prelude::*;

use crate::maths::{f2i, npmod, pmax, pmin, rint};
use crate::{need, rd, wr, zeros, Nd, NdMut};

/// A place counted from the end when below 0 (Numba's a[-1]).
#[inline(always)]
fn wrap(i: i64, n: usize) -> usize {
    if i < 0 {
        (i + n as i64) as usize
    } else {
        i as usize
    }
}

/// The key row nearest q (np.searchsorted, then the lower one on a tie): fastloops' row pick.
#[inline(always)]
fn nearest_row(keys: &[i64], q: f64) -> usize {
    let nkeys = keys.len() as i64;
    let (mut lo, mut hi) = (0i64, nkeys);
    while lo < hi {
        let h = (lo + hi) >> 1;
        if (keys[h as usize] as f64) < q {
            lo = h + 1;
        } else {
            hi = h;
        }
    }
    let r = lo.max(0).min(nkeys - 1);
    let j = (r - 1).max(0);
    let (kj, kr) = (keys[wrap(j, keys.len())] as f64, keys[wrap(r, keys.len())] as f64);
    if (kj - q).abs() <= (kr - q).abs() {
        wrap(j, keys.len())
    } else {
        wrap(r, keys.len())
    }
}

/// The numbers stir_nearest reads (fastloops._near_one's arguments past the spot).
pub struct Near<'a> {
    keys: &'a [i64],
    first: &'a [i64],
    last: &'a [i64],
    line: &'a [f64],
    ms: &'a [f64],
    nk: &'a [i64],
    nm: &'a [f64],
    o: &'a [i64],
    base: f64,
    top: f64,
    gap: f64,
    own: bool,
}

/// line[i] (negative i counted from the end, like the halving search read it).
#[inline(always)]
fn lb_at(line: &[f64], i: i64) -> f64 {
    line[wrap(i, line.len())]
}

/// The first place in lo .. hi of the sorted line holding v or more (hi: none): the plain halving search's very
/// answer (there's only one), found from a guess where v would sit if the row's notes were evenly spread, then
/// steps doubling out from it until v is between, then halving (C7: a row of spam = a few reads next to each other
/// instead of ~20 spread over megabytes; worst case about twice the halving's reads).
#[inline(always)]
fn lower_bound(line: &[f64], lo: i64, hi: i64, v: f64) -> i64 {
    // (no closures in the hot loops: see round_look)
    if hi - lo <= 16 {
        let (mut a, mut b) = (lo, hi);
        while a < b {
            let h = (a + b) >> 1;
            if lb_at(line, h) < v {
                a = h + 1;
            } else {
                b = h;
            }
        }
        return a;
    }
    let (a0, a1) = (lb_at(line, lo), lb_at(line, hi - 1));
    if !(a0 < v) {
        return lo; // (v at or before the first, or not a number: the halving ends there too)
    }
    if a1 < v {
        return hi;
    }
    if !(a1 > a0) {
        return hi - 1;
    }
    // (now line[lo] < v <= line[hi - 1]; keep line[l] < v <= line[r], the answer in l + 1 ..= r)
    let f = (v - a0) / (a1 - a0);
    let g = (lo + (f * (hi - 1 - lo) as f64) as i64).max(lo + 1).min(hi - 1);
    let (mut l, mut r);
    if lb_at(line, g) < v {
        l = g;
        let mut step = 1i64;
        r = g + 1;
        while r < hi - 1 && lb_at(line, r) < v {
            l = r;
            step <<= 1;
            r = l + step;
        }
        r = r.min(hi - 1);
    } else {
        r = g;
        let mut step = 1i64;
        l = g - 1;
        while l > lo && lb_at(line, l) >= v {
            r = l;
            step <<= 1;
            l = r - step;
        }
        l = l.max(lo);
    }
    while r - l > 1 {
        let m = (l + r) >> 1;
        if lb_at(line, m) < v {
            l = m;
        } else {
            r = m;
        }
    }
    r
}

/// fastloops._near_one for spot s (mid m, key k).
#[inline(always)]
fn near_one(s: usize, m: f64, k: f64, d: &Near) -> i64 {
    let (row, p) = near_place(m, k, d);
    let mut got = d.o[p];
    if d.own && d.nk[s] == d.keys[row] && (d.nm[s] - m).abs() <= (d.ms[p] - m).abs() + 1e-9 {
        got = s as i64;
    }
    got
}

/// near_one's (key row, place in the line) of the note nearest (m, k), the spot's own note left out.
#[inline(always)]
fn near_place(m: f64, k: f64, d: &Near) -> (usize, usize) {
    let q = rint(k);
    let row = nearest_row(d.keys, q);
    let (lo, hi) = (d.first[row], d.last[row]);
    let c = pmin(pmax(m, d.base), d.top);
    let v = c - d.base + row as f64 * d.gap;
    let a = lower_bound(d.line, lo, hi, v);
    let mut p = a.max(lo).min(hi - 1);
    let p0 = (p - 1).max(lo);
    let nms = d.ms.len();
    if (d.ms[wrap(p0, nms)] - m).abs() <= (d.ms[wrap(p, nms)] - m).abs() {
        p = p0;
    }
    (row, wrap(p, d.o.len()))
}

#[inline(always)]
fn near_range_inner(s0: usize, mid: &[f64], key: &[f64], d: &Near, out: &mut [i64]) {
    for (j, o) in out.iter_mut().enumerate() {
        *o = near_one(s0 + j, mid[j], key[j], d);
    }
}
level_builds!(fn near_range(s0: usize, mid: &[f64], key: &[f64], d: &Near, out: &mut [i64]) => near_range_inner);

/// fastloops.stir_nearest (many = shared out over the pool) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn stir_nearest(keys: &[i64], first: &[i64], last: &[i64], line: &[f64], ms: &[f64], nk: &[i64], nm: &[f64],
                    o: &[i64], base: f64, top: f64, gap: f64, mid: &[f64], key: &[f64], own: bool, out: &mut [i64],
                    many: bool) -> i64 {
    let n = mid.len();
    let (key, out) = (&key[..n], &mut out[..n]);
    let d = Near { keys, first, last, line, ms, nk, nm, o, base, top, gap, own };
    match crate::pool_for(many, n, 1) {
        None => near_range(0, mid, key, &d, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(1024);
            pool.install(|| {
                (mid.par_chunks(chunk), key.par_chunks(chunk), out.par_chunks_mut(chunk)).into_par_iter()
                    .enumerate().for_each(|(c, (mc, kc, oc))| near_range(c * chunk, mc, kc, &d, oc));
            });
        }
    }
    0
}

/// fastloops._wrap_one for one spot.
#[inline(always)]
fn wrap_one(m: f64, k: f64, keys: &[i64], a: &[f64], b: &[f64]) -> f64 {
    let row = nearest_row(keys, rint(k));
    let (s, e) = (a[row], b[row]);
    if m < s || m > e {
        return s + npmod(m - s, pmax(e - s, 1.0));
    }
    m
}

#[inline(always)]
fn wrap_range_inner(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64]) {
    // (checked once: keys not empty, a / b a number per key row, mid / key / out as long as each other: then the
    // search and reads need no checks (the row found is always 0 .. len(keys) - 1). Else the checked one)
    let (nk, n) = (keys.len(), out.len());
    if nk == 0 || a.len() < nk || b.len() < nk || mid.len() < n || key.len() < n {
        for (j, o) in out.iter_mut().enumerate() {
            *o = wrap_one(mid[j], key[j], keys, a, b);
        }
        return;
    }
    for j in 0..n {
        let q = rint(rd(key, j));
        let (mut lo, mut hi) = (0usize, nk);
        while lo < hi {
            let h = (lo + hi) >> 1;
            if (rd(keys, h) as f64) < q {
                lo = h + 1;
            } else {
                hi = h;
            }
        }
        let r = lo.min(nk - 1);
        let jj = r.saturating_sub(1);
        let (kj, kr) = (rd(keys, jj) as f64, rd(keys, r) as f64);
        let row = if (kj - q).abs() <= (kr - q).abs() { jj } else { r };
        let (m, s, e) = (rd(mid, j), rd(a, row), rd(b, row));
        wr(out, j, if m < s || m > e { s + npmod(m - s, pmax(e - s, 1.0)) } else { m });
    }
}
level_builds!(fn wrap_range(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64])
              => wrap_range_inner);

/// fastloops._bounce_one's sums: m past s .. e bounced back off that end (there and back, as far as it goes).
#[inline(always)]
fn bounce_in(m: f64, s: f64, e: f64) -> f64 {
    let w = pmax(e - s, 1.0);
    let u = npmod(m - s, 2.0 * w);
    s + if u > w { 2.0 * w - u } else { u }
}

/// fastloops._bounce_one for one spot.
#[inline(always)]
fn bounce_one(m: f64, k: f64, keys: &[i64], a: &[f64], b: &[f64]) -> f64 {
    let row = nearest_row(keys, rint(k));
    let (s, e) = (a[row], b[row]);
    if m < s || m > e {
        return bounce_in(m, s, e);
    }
    m
}

#[inline(always)]
fn bounce_range_inner(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64]) {
    // (wrap_range_inner's checks + search; bounced instead of wrapped)
    let (nk, n) = (keys.len(), out.len());
    if nk == 0 || a.len() < nk || b.len() < nk || mid.len() < n || key.len() < n {
        for (j, o) in out.iter_mut().enumerate() {
            *o = bounce_one(mid[j], key[j], keys, a, b);
        }
        return;
    }
    for j in 0..n {
        let q = rint(rd(key, j));
        let (mut lo, mut hi) = (0usize, nk);
        while lo < hi {
            let h = (lo + hi) >> 1;
            if (rd(keys, h) as f64) < q {
                lo = h + 1;
            } else {
                hi = h;
            }
        }
        let r = lo.min(nk - 1);
        let jj = r.saturating_sub(1);
        let (kj, kr) = (rd(keys, jj) as f64, rd(keys, r) as f64);
        let row = if (kj - q).abs() <= (kr - q).abs() { jj } else { r };
        let (m, s, e) = (rd(mid, j), rd(a, row), rd(b, row));
        wr(out, j, if m < s || m > e { bounce_in(m, s, e) } else { m });
    }
}
level_builds!(fn bounce_range(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64])
              => bounce_range_inner);

/// fastloops.stir_bounce (many = shared out) -> 0.
#[inline(always)]
pub fn stir_bounce(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64], many: bool) -> i64 {
    let n = mid.len();
    let (key, out) = (&key[..n], &mut out[..n]);
    match crate::pool_for(many, n, 1) {
        None => bounce_range(keys, a, b, mid, key, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(1024);
            pool.install(|| {
                (mid.par_chunks(chunk), key.par_chunks(chunk), out.par_chunks_mut(chunk)).into_par_iter()
                    .for_each(|(mc, kc, oc)| bounce_range(keys, a, b, mc, kc, oc));
            });
        }
    }
    0
}

/// fastloops.stir_wrap (many = shared out) -> 0.
#[inline(always)]
pub fn stir_wrap(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64], many: bool) -> i64 {
    let n = mid.len();
    let (key, out) = (&key[..n], &mut out[..n]);
    match crate::pool_for(many, n, 1) {
        None => wrap_range(keys, a, b, mid, key, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(1024);
            pool.install(|| {
                (mid.par_chunks(chunk), key.par_chunks(chunk), out.par_chunks_mut(chunk)).into_par_iter()
                    .for_each(|(mc, kc, oc)| wrap_range(keys, a, b, mc, kc, oc));
            });
        }
    }
    0
}

/// fastloops._sample_at for spots x / y into out (2 numbers a spot).
#[inline(always)]
fn sample_inner(a: &[f64], gy: usize, gx: usize, x: &[f64], y: &[f64], out: &mut [f64]) {
    let n = x.len();
    // (sizes checked once: fx / fy are clamped to 0 .. g - 1 first, so i0 / j0 stay in 0 .. g - 2 and every read
    // below is inside the map; a NaN spot gives 0 too. No checks inside the loop: 8 reads a spot)
    assert!(gx >= 2 && gy >= 2 && y.len() >= n && out.len() >= n * 2 && a.len() >= gy * gx * 2);
    let row = gx * 2;
    let (wx, wy) = ((gx as i64 - 1) as f64, (gy as i64 - 1) as f64);
    let (mx, my) = (gx as i64 - 2, gy as i64 - 2);
    for i in 0..n {
        unsafe {
            let fx = pmin(pmax(*x.get_unchecked(i), 0.0), 1.0) * wx;
            let fy = pmin(pmax(*y.get_unchecked(i), 0.0), 1.0) * wy;
            // (fx / fy are 0 .. g - 1 here (or NaN: 0), so the plain conversion is exact; Rust's own `as` adds
            // range checks each time: the sampler was 1.2x Numba's)
            let i0 = if fx == fx { fx.to_int_unchecked::<i64>() } else { 0 }.min(mx);
            let j0 = if fy == fy { fy.to_int_unchecked::<i64>() } else { 0 }.min(my);
            let (tx, ty) = (fx - i0 as f64, fy - j0 as f64);
            let p = j0 as usize * row + i0 as usize * 2;
            for c in 0..2 {
                let a00 = *a.get_unchecked(p + c);
                let a01 = *a.get_unchecked(p + 2 + c);
                let a10 = *a.get_unchecked(p + row + c);
                let a11 = *a.get_unchecked(p + row + 2 + c);
                *out.get_unchecked_mut(i * 2 + c) =
                    (a00 * (1.0 - tx) + a01 * tx) * (1.0 - ty) + (a10 * (1.0 - tx) + a11 * tx) * ty;
            }
        }
    }
}
level_builds!(fn sample_range(a: &[f64], gy: usize, gx: usize, x: &[f64], y: &[f64], out: &mut [f64])
              => sample_inner);

/// fastloops.liquify_sample (a: the map (gy, gx, 2); out (n, 2); many = shared out) -> 0.
#[inline(always)]
pub fn liquify_sample(a: Nd<f64, 3>, x: &[f64], y: &[f64], out: NdMut<f64, 2>, many: bool) -> i64 {
    let n = x.len();
    let (gy, gx) = (a.sh[0], a.sh[1]);
    if a.sh[2] != 2 || out.sh[1] != 2 || gy < 2 || gx < 2 {
        return crate::FAILED; // (Numba reads past the map then: its loop runs)
    }
    let (y, out) = (&y[..n], &mut out.d[..n * 2]);
    match crate::pool_for(many, n, 1) {
        None => sample_range(a.d, gy, gx, x, y, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(1024);
            pool.install(|| {
                (x.par_chunks(chunk), y.par_chunks(chunk), out.par_chunks_mut(chunk * 2)).into_par_iter()
                    .for_each(|(xc, yc, oc)| sample_range(a.d, gy, gx, xc, yc, oc));
            });
        }
    }
    0
}

// ---- the Warp (notes/warp.py, liquify.py; user 2026-11-09: heavy per-spot work compiled)

/// fastloops._sample2: the map a (gy, gx, 2) at one spot, (across, up).
#[inline(always)]
fn sample2(a: &[f64], gy: usize, gx: usize, x: f64, y: f64) -> (f64, f64) {
    let fx = pmin(pmax(x, 0.0), 1.0) * (gx as i64 - 1) as f64;
    let fy = pmin(pmax(y, 0.0), 1.0) * (gy as i64 - 1) as f64;
    let i0 = f2i(fx).min(gx as i64 - 2);
    let j0 = f2i(fy).min(gy as i64 - 2);
    let (tx, ty) = (fx - i0 as f64, fy - j0 as f64);
    let row = gx * 2;
    let p = j0 as usize * row + i0 as usize * 2;
    let mut v = [0.0f64; 2];
    for (c, vc) in v.iter_mut().enumerate() {
        let (a00, a01, a10, a11) = (a[p + c], a[p + 2 + c], a[p + row + c], a[p + row + 2 + c]);
        *vc = (a00 * (1.0 - tx) + a01 * tx) * (1.0 - ty) + (a10 * (1.0 - tx) + a11 * tx) * ty;
    }
    (v[0], v[1])
}

/// fastloops._straight_at: (value across, up, d/dx across, up, d/dy across, up) of Straight cells' offsets P
/// (gy, gx, 2), the slopes exact (value = sample2's sums).
#[inline(always)]
fn straight_at(p: &[f64], gy: usize, gx: usize, x: f64, y: f64) -> [f64; 6] {
    let fx = pmin(pmax(x, 0.0), 1.0) * (gx as i64 - 1) as f64;
    let fy = pmin(pmax(y, 0.0), 1.0) * (gy as i64 - 1) as f64;
    let i0 = f2i(fx).min(gx as i64 - 2);
    let j0 = f2i(fy).min(gy as i64 - 2);
    let (tx, ty) = (fx - i0 as f64, fy - j0 as f64);
    let mx = (if x >= 0.0 && x <= 1.0 { 1.0 } else { 0.0 }) * (gx as i64 - 1) as f64;
    let my = (if y >= 0.0 && y <= 1.0 { 1.0 } else { 0.0 }) * (gy as i64 - 1) as f64;
    let row = gx * 2;
    let q = j0 as usize * row + i0 as usize * 2;
    let mut out = [0.0f64; 6];
    for c in 0..2 {
        let (a, b, cc, d) = (p[q + c], p[q + 2 + c], p[q + row + c], p[q + row + 2 + c]);
        let (lo, hi) = (a * (1.0 - tx) + b * tx, cc * (1.0 - tx) + d * tx);
        out[c] = lo * (1.0 - ty) + hi * ty;
        out[2 + c] = ((b - a) * (1.0 - ty) + (d - cc) * ty) * mx;
        out[4 + c] = (hi - lo) * my;
    }
    out
}

/// fastloops._eased_part: (cell, eased share, its slope) of one number along g points.
#[inline(always)]
fn eased_part(v: f64, g: usize) -> (i64, f64, f64) {
    let f = pmin(pmax(v, 0.0), 1.0) * (g as i64 - 1) as f64;
    let i = f2i(f).min(g as i64 - 2);
    let t = f - i as f64;
    let s = t * t * (3.0 - 2.0 * t);
    let m = if v >= 0.0 && v <= 1.0 { 1.0 } else { 0.0 };
    (i, s, 6.0 * t * (1.0 - t) * m * (g as i64 - 1) as f64)
}

/// fastloops._eased_at: (value across, up, d/dx across, up, d/dy across, up) of the offsets P (gy, gx, 2).
#[inline(always)]
fn eased_at(p: &[f64], gy: usize, gx: usize, x: f64, y: f64) -> [f64; 6] {
    let (i, sx, dx) = eased_part(x, gx);
    let (j, sy, dy) = eased_part(y, gy);
    let row = gx * 2;
    let q = j as usize * row + i as usize * 2;
    let mut out = [0.0f64; 6];
    for c in 0..2 {
        let (a, b, cc, d) = (p[q + c], p[q + 2 + c], p[q + row + c], p[q + row + 2 + c]);
        let (lo, hi) = (a + (b - a) * sx, cc + (d - cc) * sx);
        out[c] = lo + (hi - lo) * sy;
        out[2 + c] = ((b - a) + ((d - cc) - (b - a)) * sy) * dx;
        out[4 + c] = (hi - lo) * dy;
    }
    out
}

/// fastloops._bern_part: (cell, 4 bicubic weights, their 4 slopes) of one number along n cells.
#[inline(always)]
fn bern_part(v: f64, n: usize) -> (i64, [f64; 4], [f64; 4]) {
    let f = pmin(pmax(v, 0.0), 1.0) * n as f64;
    let i = f2i(f).min(n as i64 - 1);
    let t = f - i as f64;
    let u = 1.0 - t;
    let m = (if v >= 0.0 && v <= 1.0 { 1.0 } else { 0.0 }) * n as f64;
    (i, [u * u * u, 3.0 * t * u * u, 3.0 * t * t * u, t * t * t],
     [-3.0 * u * u * m, 3.0 * u * (u - 2.0 * t) * m, 3.0 * t * (2.0 * u - t) * m, 3.0 * t * t * m])
}

/// fastloops._net_at: (value across, up, d/dx across, up, d/dy across, up) of the control net C (gy, gx, 2).
#[inline(always)]
fn net_at(p: &[f64], gy: usize, gx: usize, x: f64, y: f64) -> [f64; 6] {
    let (i, wx, dx) = bern_part(x, (gx - 1) / 3);
    let (j, wy, dy) = bern_part(y, (gy - 1) / 3);
    let row = gx * 2;
    let mut out = [0.0f64; 6];
    for b in 0..4 {
        let q0 = (3 * j as usize + b) * row + 3 * i as usize * 2;
        for c in 0..2 {
            let (e0, e1, e2, e3) = (p[q0 + c], p[q0 + 2 + c], p[q0 + 4 + c], p[q0 + 6 + c]);
            let rb = ((e0 * wx[0] + e1 * wx[1]) + e2 * wx[2]) + e3 * wx[3];
            let sb = ((e0 * dx[0] + e1 * dx[1]) + e2 * dx[2]) + e3 * dx[3];
            out[c] = out[c] + rb * wy[b];
            out[2 + c] = out[2 + c] + sb * wy[b];
            out[4 + c] = out[4 + c] + rb * dy[b];
        }
    }
    out
}

/// fastloops._even_at: (a number's place on the even grid of the lines l, the stretch du / dv); l empty = even.
#[inline(always)]
fn even_at(v: f64, l: &[f64]) -> (f64, f64) {
    if l.len() < 2 || v < 0.0 || v > 1.0 {
        return (v, 1.0);
    }
    let n = l.len() - 1;
    let (mut lo, mut hi) = (0usize, n - 1);
    while lo < hi {
        let m = (lo + hi + 1) >> 1;
        if l[m] <= v {
            lo = m;
        } else {
            hi = m - 1;
        }
    }
    let w = l[lo + 1] - l[lo];
    ((lo as f64 + (v - l[lo]) / w) / n as f64, 1.0 / (n as f64 * w))
}

/// The numbers warp_newton reads past the spots.
struct Newton<'a> {
    p: &'a [f64],
    gy: usize,
    gx: usize,
    kind: i64,
    lx: &'a [f64],
    ly: &'a [f64],
    bounds: &'a [f64],
    has_b: bool,
    steps: i64,
    give_step: i64,
    give_far: f64,
    lazy: f64, // (newton_jac's lazy finish: 0 = off)
}

/// fastloops._newton_one for spot i (px / py / sx / sy its own numbers): (x, y, how far off).
#[inline(always)]
fn newton_one(i: usize, px: f64, py: f64, sx: f64, sy: f64, d: &Newton) -> (f64, f64, f64) {
    let (x, y, b, _) = newton_jac(i, px, py, sx, sy, d);
    (x, y, b)
}

/// fastloops._newton_lazy: newton_one + the matrix Newton inverts [a, bb, c, e] at the best point (the bend's slopes
/// there: what a small step away from the answer lands on). d.lazy > 0 (warp_soft2 only): a step taken from a spot
/// already that close (and a matrix that can be turned round) is the answer without looking again, its "how far
/// off" err * err (Newton's next step: within 1e-9 from there).
#[inline(always)]
fn newton_jac(i: usize, px: f64, py: f64, sx: f64, sy: f64, d: &Newton) -> (f64, f64, f64, [f64; 4]) {
    let (mut x, mut y) = (sx, sy);
    let mut b = f64::INFINITY;
    let (mut ox, mut oy) = (x, y);
    let mut jb = [1.0, 0.0, 0.0, 1.0];
    for step in 0..d.steps {
        let (ex, fx) = even_at(x, d.lx);
        let (ey, fy) = even_at(y, d.ly);
        let e = if d.kind == 2 {
            net_at(d.p, d.gy, d.gx, ex, ey)
        } else if d.kind != 0 {
            eased_at(d.p, d.gy, d.gx, ex, ey)
        } else {
            straight_at(d.p, d.gy, d.gx, ex, ey)
        };
        let (vx, vy, mut dxx, mut dxy, mut dyx, mut dyy) = (e[0], e[1], e[2], e[3], e[4], e[5]);
        if !d.lx.is_empty() {
            (dxx, dxy) = (dxx * fx, dxy * fx);
        }
        if !d.ly.is_empty() {
            (dyx, dyy) = (dyx * fy, dyy * fy);
        }
        let (rx, ry) = (x + vx - px, y + vy - py);
        let err = (rx * rx + ry * ry).sqrt();
        let (a, bb, c, e) = (1.0 + dxx, dyx, dxy, 1.0 + dyy);
        if err < b {
            (b, ox, oy) = (err, x, y);
            jb = [a, bb, c, e];
        }
        let mut go = err > 1e-9;
        if d.has_b && step == d.give_step {
            go = go && err < d.give_far;
        }
        if !go {
            break;
        }
        let det = a * e - bb * c;
        let (ux, uy) = if det.abs() > 1e-6 { ((e * rx - bb * ry) / det, (a * ry - c * rx) / det) } else { (rx, ry) };
        let st = (ux * ux + uy * uy).sqrt();
        let cut = if st > 0.2 { 0.2 / pmax(st, 1e-300) } else { 1.0 };
        let (mut nx, mut ny) = (x - ux * cut, y - uy * cut);
        if d.has_b {
            let k = i * 4;
            nx = pmin(pmax(nx, d.bounds[k]), d.bounds[k + 1]);
            ny = pmin(pmax(ny, d.bounds[k + 2]), d.bounds[k + 3]);
        }
        if d.lazy > 0.0 && err <= d.lazy && det.abs() > 1e-6 {
            return (nx, ny, err * err, jb);
        }
        (x, y) = (nx, ny);
    }
    (ox, oy, b, jb)
}

#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn newton_range_inner(s0: usize, px: &[f64], py: &[f64], sx: &[f64], sy: &[f64], d: &Newton, bx: &mut [f64],
                      by: &mut [f64], best: &mut [f64]) {
    for j in 0..px.len() {
        let (x, y, e) = newton_one(s0 + j, px[j], py[j], sx[j], sy[j], d);
        bx[j] = x;
        by[j] = y;
        best[j] = e;
    }
}
level_builds!(fn newton_range(s0: usize, px: &[f64], py: &[f64], sx: &[f64], sy: &[f64], d: &Newton,
                              bx: &mut [f64], by: &mut [f64], best: &mut [f64]) => newton_range_inner);

/// fastloops.warp_newton (P: the offsets (gy, gx, 2), or with kind 2 a control net (3 rows + 1, 3 cols + 1, 2);
/// kind 0 Straight cells, 1 Smooth; lx / ly a split grid's lines (empty = even); bounds (n, 4) read when has_b;
/// many = shared out) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn warp_newton(p: Nd<f64, 3>, px: &[f64], py: &[f64], kind: i64, lx: &[f64], ly: &[f64], sx: &[f64], sy: &[f64],
                   bounds: Nd<f64, 2>,
                   has_b: bool, steps: i64, give_step: i64, give_far: f64, bx: &mut [f64], by: &mut [f64],
                   best: &mut [f64], many: bool) -> i64 {
    let n = px.len();
    let (gy, gx) = (p.sh[0], p.sh[1]);
    if p.sh[2] != 2 || gy < 2 || gx < 2 || (has_b && (bounds.sh[1] != 4 || bounds.sh[0] < n))
        || !(0..=2).contains(&kind) || (kind == 2 && (gy < 4 || gx < 4 || (gy - 1) % 3 != 0 || (gx - 1) % 3 != 0))
        || lx.len() == 1 || ly.len() == 1 {
        return crate::FAILED;
    }
    let (py, sx, sy) = (&py[..n], &sx[..n], &sy[..n]);
    let (bx, by, best) = (&mut bx[..n], &mut by[..n], &mut best[..n]);
    let d = Newton { p: p.d, gy, gx, kind, lx, ly, bounds: bounds.d, has_b, steps, give_step, give_far, lazy: 0.0 };
    match crate::pool_for(many, n, 1) {
        None => newton_range(0, px, py, sx, sy, &d, bx, by, best),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(256);
            pool.install(|| {
                (px.par_chunks(chunk), py.par_chunks(chunk), sx.par_chunks(chunk), sy.par_chunks(chunk),
                 bx.par_chunks_mut(chunk), by.par_chunks_mut(chunk), best.par_chunks_mut(chunk))
                    .into_par_iter().enumerate()
                    .for_each(|(c, (a, b, e, f, g, h, k))| newton_range(c * chunk, a, b, e, f, &d, g, h, k));
            });
        }
    }
    0
}

/// fastloops._landed: the spot's map cell is one a fold lands on (near: (gy - 1) x (gx - 1), or empty).
#[inline(always)]
fn landed(near: &[u8], gy: usize, gx: usize, x: f64, y: f64) -> bool {
    if near.is_empty() {
        return false;
    }
    let ci = f2i(pmin(pmax(x, 0.0), 1.0) * (gx as i64 - 1) as f64).min(gx as i64 - 2);
    let cj = f2i(pmin(pmax(y, 0.0), 1.0) * (gy as i64 - 1) as f64).min(gy as i64 - 2);
    near[cj as usize * (gx - 1) + ci as usize] != 0
}

/// fastloops._exact_one: warp.exact_offsets for spot j: the map's answer, then Newton's steps from it; (offset
/// across, up), or NaN when it doesn't settle or its map cell is one a fold lands on (near: not solved at all, its
/// answer would be thrown away).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn exact_range_inner(x: &[f64], y: &[f64], a: &[f64], gy: usize, gx: usize, near: &[u8], d: &Newton,
                     ox: &mut [f64], oy: &mut [f64]) {
    for j in 0..x.len() {
        let (x0, y0) = (x[j], y[j]);
        (ox[j], oy[j]) = (f64::NAN, f64::NAN);
        if landed(near, gy, gx, x0, y0) {
            continue;
        }
        let (d0, d1) = sample2(a, gy, gx, x0, y0);
        let (sx, sy, e) = newton_one(0, x0, y0, x0 + d0, y0 + d1, d);
        if e < 1e-7 {
            (ox[j], oy[j]) = (sx - x0, sy - y0);
        }
    }
}
level_builds!(fn exact_range(x: &[f64], y: &[f64], a: &[f64], gy: usize, gx: usize, near: &[u8], d: &Newton,
                             ox: &mut [f64], oy: &mut [f64]) => exact_range_inner);

/// fastloops.warp_exact (p / kind / lx / ly as warp_newton's; a: the turned-round map (my, mx, 2); near: its cells
/// a fold lands on ((my - 1) x (mx - 1), or empty); many = shared out) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn warp_exact(p: Nd<f64, 3>, kind: i64, lx: &[f64], ly: &[f64], a: Nd<f64, 3>, near: &[u8], x: &[f64],
                  y: &[f64], steps: i64, ox: &mut [f64], oy: &mut [f64], many: bool) -> i64 {
    let n = x.len();
    let (gy, gx) = (p.sh[0], p.sh[1]);
    let (my, mx) = (a.sh[0], a.sh[1]);
    if p.sh[2] != 2 || gy < 2 || gx < 2 || !(0..=2).contains(&kind)
        || (kind == 2 && (gy < 4 || gx < 4 || (gy - 1) % 3 != 0 || (gx - 1) % 3 != 0))
        || lx.len() == 1 || ly.len() == 1 || a.sh[2] != 2 || my < 2 || mx < 2
        || !(near.is_empty() || near.len() == (my - 1) * (mx - 1)) || y.len() < n || ox.len() < n || oy.len() < n {
        return crate::FAILED;
    }
    let (y, ox, oy) = (&y[..n], &mut ox[..n], &mut oy[..n]);
    let d = Newton { p: p.d, gy, gx, kind, lx, ly, bounds: &[], has_b: false, steps, give_step: 0, give_far: 0.0,
                     lazy: 0.0 };
    match crate::pool_for(many, n, 1) {
        None => exact_range(x, y, a.d, my, mx, near, &d, ox, oy),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(256);
            pool.install(|| {
                (x.par_chunks(chunk), y.par_chunks(chunk), ox.par_chunks_mut(chunk), oy.par_chunks_mut(chunk))
                    .into_par_iter().for_each(|(xc, yc, oa, ob)| exact_range(xc, yc, a.d, my, mx, near, &d, oa, ob));
            });
        }
    }
    0
}

/// fastloops._exact3_one: warp.exact3_offsets for the spot (x0, y0) and the ends of its two arrows ((x0, y0) +
/// arrows[0] / [1]): [middle across, up, end 1 across, up, end 2 across, up] offsets (NaN: the map's). The middle is
/// Newton's answer from the map's, as warp_exact's; each end starts where the middle's answer + the bend's slopes
/// there say it lands (s(p + v) ~ s(p) + J^-1 v: 1 look instead of ~3), from the map's answer when that can't be
/// used or doesn't settle. d.lazy on (newton_jac). Spots a fold lands on: not solved (NaN), as warp_exact's.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn exact3_spot(x0: f64, y0: f64, arrows: [(f64, f64); 2], a: &[f64], gy: usize, gx: usize, near: &[u8], d: &Newton)
               -> [f64; 6] {
    let mut out = [f64::NAN; 6];
    let (mut sx, mut sy, mut det) = (0.0, 0.0, 0.0);
    let mut jc = [1.0, 0.0, 0.0, 1.0];
    let mut guess = false;
    if !landed(near, gy, gx, x0, y0) {
        let (d0, d1) = sample2(a, gy, gx, x0, y0);
        let (bx, by, e, j) = newton_jac(0, x0, y0, x0 + d0, y0 + d1, d);
        if e < 1e-7 {
            (out[0], out[1]) = (bx - x0, by - y0);
            (sx, sy, jc) = (bx, by, j);
            det = jc[0] * jc[3] - jc[1] * jc[2];
            guess = det.abs() > 1e-6;
        }
    }
    let [ja, jb, jcc, je] = jc;
    for q in 0..2 {
        let (vx, vy) = arrows[q];
        let (px, py) = (x0 + vx, y0 + vy);
        if landed(near, gy, gx, px, py) {
            continue;
        }
        if guess {
            let (ux, uy) = ((je * vx - jb * vy) / det, (ja * vy - jcc * vx) / det);
            let (ex, ey, e) = newton_one(0, px, py, sx + ux, sy + uy, d);
            if e < 1e-7 {
                (out[2 + 2 * q], out[3 + 2 * q]) = (ex - px, ey - py);
                continue;
            }
        }
        let (e0, e1) = sample2(a, gy, gx, px, py);
        let (ex, ey, e) = newton_one(0, px, py, px + e0, py + e1, d);
        if e < 1e-7 {
            (out[2 + 2 * q], out[3 + 2 * q]) = (ex - px, ey - py);
        }
    }
    out
}

/// fastloops._unit_back: a spot of the colour box -> (middle in ticks, key) (liquify._placed + from_unit).
#[inline(always)]
fn unit_back(mut x: f64, mut y: f64, fp: &[f64]) -> (f64, f64) {
    if fp[0] != 2.0 {
        // (2 = Wrap around: neither tiled nor clipped, the rows below bring it back)
        if fp[0] != 0.0 {
            if x < -1e-9 || x > 1.0 + 1e-9 {
                x = npmod(x, 1.0);
            }
            if y < -1e-9 || y > 1.0 + 1e-9 {
                y = npmod(y, 1.0);
            }
        }
        x = pmin(pmax(x, 0.0), 1.0);
        y = pmin(pmax(y, 0.0), 1.0);
    }
    if fp[1] != 0.0 {
        let x2 = fp[2] + x * fp[3];
        let y2 = if fp[6] != 0.0 { fp[4] + y * fp[5] } else { y };
        (x, y) = (0.5 + fp[8] * (y2 - 0.5), 0.5 + fp[7] * (x2 - 0.5));
    }
    let m = fp[9] + x * fp[10];
    let mut k = if fp[13] != 0.0 { fp[11] + y * fp[12] } else { fp[11] };
    if fp.len() > 15 && (fp[14] == 1.0 || fp[14] == 2.0 || fp[14] == 3.0) {
        // (Wrap around: fp[15] rows, then their keys, first starts, repeat ends (checked by warp_soft): the key round
        // the key rows (Rows.key_wrapped), then wrap_one's sums; 2.0 / 3.0 = Bounce back / edge twice:
        // key_bounced + bounce_in, the rows' numbers = their bounce lines)
        let bounce = fp[14] != 1.0;
        let nr = fp[15] as usize;
        let keys = &fp[16..16 + nr];
        if bounce {
            // (the lowest / highest key line after the rows' numbers: Rows.bounce_of's, on the colours' runs)
            let (base, h) = if fp.len() >= 16 + 3 * nr + 2 {
                (fp[16 + 3 * nr], fp[16 + 3 * nr + 1] - fp[16 + 3 * nr])
            } else if fp[14] == 3.0 {
                (keys[0] - 0.5, (keys[nr - 1] - keys[0]) + 1.0)
            } else {
                (keys[0], keys[nr - 1] - keys[0])
            };
            if k < base || k > base + h {
                if h <= 0.0 {
                    k = base;
                } else {
                    let u = npmod(k - base, 2.0 * h);
                    k = base + if u > h { 2.0 * h - u } else { u };
                }
            }
        } else {
            let base = keys[0] - 0.5;
            // (Repeat the colours: its key height after the rows' numbers)
            let h = if fp.len() > 16 + 3 * nr { fp[16 + 3 * nr] } else { (keys[nr - 1] - keys[0]) + 1.0 };
            if k < base || k >= base + h {
                k = base + npmod(k - base, h);
            }
        }
        let q = rint(k);
        let (mut lo, mut hi) = (0usize, nr);
        while lo < hi {
            let h = (lo + hi) >> 1;
            if keys[h] < q {
                lo = h + 1;
            } else {
                hi = h;
            }
        }
        let r = lo.min(nr - 1);
        let j = r.saturating_sub(1);
        let row = if (keys[j] - q).abs() <= (keys[r] - q).abs() { j } else { r };
        let (s, e) = (fp[16 + nr + row], fp[16 + 2 * nr + row]);
        if m < s || m > e {
            if bounce {
                return (bounce_in(m, s, e), k);
            }
            return (s + npmod(m - s, pmax(e - s, 1.0)), k);
        }
    }
    (m, k)
}

/// unit_back's Wrap around rows in fp sound: a whole row count >= 1 and that many keys / starts / ends after it.
fn wrap_rows_ok(fp: &[f64]) -> bool {
    if fp.len() <= 15 || (fp[14] != 1.0 && fp[14] != 2.0 && fp[14] != 3.0) {
        return true;
    }
    let r = fp[15];
    r >= 1.0 && r == r.trunc() && r < 1e9 && fp.len() >= 16 + 3 * (r as usize)
}

const ROUND_INNER: usize = 7; // (fastloops.ROUND_INNER: look 0 the middle, 1 .. 6 the inner ring)
const MOST_LOOKS: usize = 16;

/// round_one's look k: its place in the line.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn round_look(sx: f64, sy: f64, r: f64, ring: &[f64], k: usize, fp: &[f64], d: &Near) -> usize {
    let (m, kk) = unit_back(sx + ring[k * 3] * r, sy + ring[k * 3 + 1] * r, fp);
    near_place(m, kk, d).1
}

/// fastloops._round_at: a stretched spot's looks round where it fetches from (sx, sy) vote.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn round_one(sx: f64, sy: f64, r: f64, ring: &[f64], nl: usize, fp: &[f64], d: &Near, cl: &Colours) -> i64 {
    // (got = places in the line; their colours from cl; the note handed back = o of the winner's place)
    let mut got = [0usize; MOST_LOOKS];
    let mut col = [0i64; MOST_LOOKS];
    // (C7: a plain function, never a closure: a closure isn't built for the processor level the loop runs at,
    // so its rint became a slow library call, ~60 ns a look)
    got[0] = round_look(sx, sy, r, ring, 0, fp, d);
    col[0] = cl.at(got[0]);
    let mut same = true;
    for k in ROUND_INNER..nl {
        got[k] = round_look(sx, sy, r, ring, k, fp, d);
        col[k] = cl.at(got[k]);
        same &= col[k] == col[0];
    }
    if same {
        return cl.o[got[0]];
    }
    for k in 1..ROUND_INNER {
        got[k] = round_look(sx, sy, r, ring, k, fp, d);
        col[k] = cl.at(got[k]);
    }
    let mut cols = [0i64; MOST_LOOKS];
    let mut score = [0.0f64; MOST_LOOKS];
    let mut nc = 0usize;
    for k in 0..nl {
        let c = col[k];
        let mut q = 0;
        while q < nc && cols[q] != c {
            q += 1;
        }
        if q == nc {
            cols[q] = c;
            nc += 1;
        }
        score[q] += ring[k * 3 + 2];
    }
    score[0] += 1e-6;
    let mut best = 0;
    for q in 1..nc {
        if score[q] > score[best] || (score[q] == score[best] && cols[q] < cols[best]) {
            best = q;
        }
    }
    for k in 0..nl {
        if col[k] == cols[best] {
            return cl.o[got[k]];
        }
    }
    cl.o[got[0]]
}

/// The colour of each place in the line (the votes count places; the note handed back = o of the winner's).
struct Colours<'a> {
    now: &'a [i64],
    o: &'a [i64],
}

impl Colours<'_> {
    #[inline(always)]
    fn at(&self, p: usize) -> i64 {
        self.now[self.o[p] as usize]
    }
}

/// fastloops._cover_at: a squashed note's nt x nk looks (tstart / ta / tb) vote, the most common colour wins.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn cover_one(mid: f64, key: f64, vt: (f64, f64), vk: (f64, f64), nt: i64, nk: i64, tstart: &[i64], ta: &[f64],
             tb: &[f64], d: &Near, cl: &Colours) -> i64 {
    let t = ((nt - 1) * 4 + (nk - 1)) as usize;
    let (a0, a1) = (tstart[t] as usize, tstart[t + 1] as usize);
    let s = a1 - a0;
    let mut got = [0usize; MOST_LOOKS];
    for j in 0..s {
        let pm = mid + ta[a0 + j] * vt.0 + tb[a0 + j] * vk.0;
        let pk = key + ta[a0 + j] * vt.1 + tb[a0 + j] * vk.1;
        got[j] = near_place(pm, pk, d).1;
    }
    if s == 1 {
        return cl.o[got[0]];
    }
    let mut cols = [0i64; MOST_LOOKS];
    let mut count = [0i64; MOST_LOOKS];
    let mut firstj = [0i64; MOST_LOOKS];
    let mut nc = 0usize;
    for j in 0..s {
        let c = cl.at(got[j]);
        let mut q = 0;
        while q < nc && cols[q] != c {
            q += 1;
        }
        if q == nc {
            cols[q] = c;
            firstj[q] = j as i64;
            nc += 1;
        }
        count[q] += 1;
    }
    let s1 = s as i64 + 1;
    let mut best = 0;
    for q in 1..nc {
        let (vq, vb) = (count[q] * s1 - firstj[q], count[best] * s1 - firstj[best]);
        if vq > vb || (vq == vb && cols[q] < cols[best]) {
            best = q;
        }
    }
    cl.o[got[firstj[best] as usize]]
}

/// The numbers warp_soft reads past the notes.
struct Soft<'a> {
    at: &'a [i64],
    cols: usize,
    a: &'a [f64],
    gy: usize,
    gx: usize,
    c: &'a [f64],
    fp: &'a [f64],
    ring: &'a [f64],
    nl: usize,
    tstart: &'a [i64],
    ta: &'a [f64],
    tb: &'a [f64],
    cl: Colours<'a>,
}

/// Where the spot (x, y) fetches from: its exact fetch (ox, oy), or the map's, blended (NaN).
#[inline(always)]
fn fetch(x: f64, y: f64, ox: f64, oy: f64, s100: f64, s: &Soft) -> (f64, f64) {
    if ox.is_nan() {
        let (d0, d1) = sample2(s.a, s.gy, s.gx, x, y);
        (x + d0 * s100, y + d1 * s100)
    } else {
        (x + ox, y + oy)
    }
}

/// fastloops._soft_one: note j at spot (x0, y0) of the box -> the note it takes its colour from. o: the exact
/// fetches (a Warp's, warp.exact_offsets; NaN = the map's) of the spot, of the end of the note's length arrow and of
/// its key arrow, as (x, y) pairs.
#[inline(always)]
fn soft_one(j: usize, x0: f64, y0: f64, o: &[f64; 6], s: &Soft, d: &Near) -> i64 {
    let s100 = s.c[5];
    let (sx, sy) = fetch(x0, y0, o[0], o[1], s100, s);
    let (mid, key) = unit_back(sx, sy, s.fp);
    if s.c[8] != 0.0 {
        return near_one(0, mid, key, d); // (Edges: Sharp, the Liquify: the nearest note alone, C7)
    }
    let (st, en) = (s.at[j * s.cols] as f64, s.at[j * s.cols + 1] as f64);
    let h = pmax((en - st) / 2.0, 0.5);
    let (xx, yy) = fetch(x0 + s.c[0] * h, y0 + s.c[1] * h, o[2], o[3], s100, s);
    let (m1, k1) = unit_back(xx, yy, s.fp);
    let (xx, yy) = fetch(x0 + s.c[2], y0 + s.c[3], o[4], o[5], s100, s);
    let (m3, k3) = unit_back(xx, yy, s.fp);
    let (vt0, vt1) = ((m1 - mid) * 2.0, (k1 - key) * 2.0);
    let (vk0, vk1) = ((m3 - mid) * 2.0, (k3 - key) * 2.0);
    let (ut, uk) = (vt0 / (2.0 * h), vt1);
    let (wt, wk) = (vk0 / (2.0 * h), vk1);
    let area = (ut * wk - uk * wt).abs();
    if area <= s.c[6] {
        let grow = pmin(pmax(1.0 / pmax(area, 1e-12).sqrt() - 1.0, 0.0), 1.0);
        if grow < 0.05 {
            return near_one(0, mid, key, d);
        }
        return round_one(sx, sy, s.c[4] * s.c[7] * grow, s.ring, s.nl, s.fp, d, &s.cl);
    }
    let nt = pmin(pmax((ut * ut + uk * uk).sqrt().ceil(), 1.0), 4.0) as i64;
    let nk = pmin(pmax((wt * wt + wk * wk).sqrt().ceil(), 1.0), 4.0) as i64;
    cover_one(mid, key, (vt0, vt1), (vk0, vk1), nt, nk, s.tstart, s.ta, s.tb, d, &s.cl)
}

#[inline(always)]
fn soft_range_inner(idx: &[i64], x: &[f64], y: &[f64], ox: &[f64], oy: &[f64], s: &Soft, d: &Near,
                    out: &mut [i64]) {
    // (ox / oy: empty = every spot the map's; one a spot = the middle's; three a spot (interleaved) = the middle's,
    // the length arrow's end's, the key arrow's end's)
    let n = out.len();
    let per = if ox.is_empty() || n == 0 { 0 } else { ox.len() / n };
    let nan = f64::NAN;
    for j in 0..n {
        let o = match per {
            0 => [nan; 6],
            1 => [ox[j], oy[j], nan, nan, nan, nan],
            _ => [ox[3 * j], oy[3 * j], ox[3 * j + 1], oy[3 * j + 1], ox[3 * j + 2], oy[3 * j + 2]],
        };
        out[j] = soft_one(idx[j] as usize, x[j], y[j], &o, s, d);
    }
}
level_builds!(fn soft_range(idx: &[i64], x: &[f64], y: &[f64], ox: &[f64], oy: &[f64], s: &Soft, d: &Near,
                            out: &mut [i64]) => soft_range_inner);

/// fastloops.warp_soft (at: the notes' places; idx: the notes picked for; a: the map (gy, gx, 2); c / fp: the
/// numbers _soft_one / _unit_back read; ring (looks, 3); ox / oy: each spot's exact fetch (NaN: the map's), one a
/// spot, or three a spot when c[9] = 1 (also the note's two arrow ends, interleaved), or both empty; many = shared
/// out) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn warp_soft(at: Nd<i64, 2>, idx: &[i64], x: &[f64], y: &[f64], ox: &[f64], oy: &[f64], a: Nd<f64, 3>,
                 c: &[f64], fp: &[f64],
                 ring: Nd<f64, 2>, tstart: &[i64], ta: &[f64], tb: &[f64], keys: &[i64], first: &[i64],
                 last: &[i64], line: &[f64], ms: &[f64], o: &[i64], base: f64, top: f64, gap: f64, now: &[i64],
                 out: &mut [i64], many: bool) -> i64 {
    let n = idx.len();
    let (rows, cols) = (at.sh[0], at.sh[1]);
    let (gy, gx) = (a.sh[0], a.sh[1]);
    let nl = ring.sh[0];
    if cols < 2 || a.sh[2] != 2 || gy < 2 || gx < 2 || ring.sh[1] != 3 || nl > MOST_LOOKS || nl < ROUND_INNER
        || c.len() < 9 || fp.len() < 14 || !wrap_rows_ok(fp) || keys.is_empty() || tstart.len() < 17 || x.len() < n || y.len() < n
        || out.len() < n || ox.len() != oy.len() {
        return crate::FAILED;
    }
    let per = if ox.is_empty() { 0 } else if c.len() > 9 && c[9] != 0.0 { 3 } else { 1 };
    if ox.len() < per * n {
        return crate::FAILED;
    }
    for j in 0..16 {
        // (each count's looks fit the small tables)
        if tstart[j] < 0 || tstart[j + 1] < tstart[j] || tstart[j + 1] - tstart[j] > MOST_LOOKS as i64
            || tstart[j + 1] as usize > ta.len() || tstart[j + 1] as usize > tb.len() {
            return crate::FAILED;
        }
    }
    if idx.iter().any(|&j| j < 0 || j as usize >= rows) {
        return crate::FAILED;
    }
    let (x, y, out) = (&x[..n], &y[..n], &mut out[..n]);
    let d = Near { keys, first, last, line, ms, nk: &[], nm: &[], o, base, top, gap, own: false };
    let cl = Colours { now, o };
    let s = Soft { at: at.d, cols, a: a.d, gy, gx, c, fp, ring: ring.d, nl, tstart, ta, tb, cl };
    let has = per > 0;
    let (ox, oy) = if has { (&ox[..per * n], &oy[..per * n]) } else { (ox, oy) };
    match crate::pool_for(many, n, 1) {
        None => soft_range(idx, x, y, ox, oy, &s, &d, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(256);
            pool.install(|| {
                if has {
                    (idx.par_chunks(chunk), x.par_chunks(chunk), y.par_chunks(chunk), ox.par_chunks(chunk * per),
                     oy.par_chunks(chunk * per), out.par_chunks_mut(chunk))
                        .into_par_iter()
                        .for_each(|(i, xs, ys, a, b, oc)| soft_range(i, xs, ys, a, b, &s, &d, oc));
                } else {
                    (idx.par_chunks(chunk), x.par_chunks(chunk), y.par_chunks(chunk), out.par_chunks_mut(chunk))
                        .into_par_iter().for_each(|(i, xs, ys, oc)| soft_range(i, xs, ys, &[], &[], &s, &d, oc));
                }
            });
        }
    }
    0
}

/// fastloops._soft2_one: soft_one with the note's three exact fetches (its middle + its two arrows' ends, the arrows
/// as soft_one reads them from c) worked out right here (exact3_spot): nothing handed over in big arrays.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn soft2_range_inner(idx: &[i64], x: &[f64], y: &[f64], s: &Soft, d: &Near, nw: &Newton, near: &[u8],
                     out: &mut [i64]) {
    for j in 0..out.len() {
        let jj = idx[j] as usize;
        let (st, en) = (s.at[jj * s.cols] as f64, s.at[jj * s.cols + 1] as f64);
        let h = pmax((en - st) / 2.0, 0.5);
        let o = exact3_spot(x[j], y[j], [(s.c[0] * h, s.c[1] * h), (s.c[2], s.c[3])], s.a, s.gy, s.gx, near, nw);
        out[j] = soft_one(jj, x[j], y[j], &o, s, d);
    }
}
level_builds!(fn soft2_range(idx: &[i64], x: &[f64], y: &[f64], s: &Soft, d: &Near, nw: &Newton, near: &[u8],
                             out: &mut [i64]) => soft2_range_inner);

/// fastloops.warp_soft2 = warp_soft (Edges: Smooth, c[8] = 0; no per-note arrows) with the Warp's exact fetches
/// worked out inside, three a note by exact3_spot (p / kind / lx / ly / near / steps as warp_exact's, lazy as
/// newton_jac's) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn warp_soft2(at: Nd<i64, 2>, idx: &[i64], x: &[f64], y: &[f64], a: Nd<f64, 3>, c: &[f64], fp: &[f64],
                  ring: Nd<f64, 2>, tstart: &[i64], ta: &[f64], tb: &[f64], keys: &[i64], first: &[i64],
                  last: &[i64], line: &[f64], ms: &[f64], o: &[i64], base: f64, top: f64, gap: f64, now: &[i64],
                  p: Nd<f64, 3>, kind: i64, lx: &[f64], ly: &[f64], near: &[u8], steps: i64, lazy: f64,
                  out: &mut [i64], many: bool) -> i64 {
    let n = idx.len();
    let (rows, cols) = (at.sh[0], at.sh[1]);
    let (gy, gx) = (a.sh[0], a.sh[1]);
    let nl = ring.sh[0];
    let (pgy, pgx) = (p.sh[0], p.sh[1]);
    if cols < 2 || a.sh[2] != 2 || gy < 2 || gx < 2 || ring.sh[1] != 3 || nl > MOST_LOOKS || nl < ROUND_INNER
        || c.len() < 9 || fp.len() < 14 || !wrap_rows_ok(fp) || keys.is_empty() || tstart.len() < 17 || x.len() < n
        || y.len() < n || out.len() < n || p.sh[2] != 2 || pgy < 2 || pgx < 2 || !(0..=2).contains(&kind)
        || (kind == 2 && (pgy < 4 || pgx < 4 || (pgy - 1) % 3 != 0 || (pgx - 1) % 3 != 0))
        || lx.len() == 1 || ly.len() == 1 || !(near.is_empty() || near.len() == (gy - 1) * (gx - 1)) {
        return crate::FAILED;
    }
    for j in 0..16 {
        if tstart[j] < 0 || tstart[j + 1] < tstart[j] || tstart[j + 1] - tstart[j] > MOST_LOOKS as i64
            || tstart[j + 1] as usize > ta.len() || tstart[j + 1] as usize > tb.len() {
            return crate::FAILED;
        }
    }
    if idx.iter().any(|&j| j < 0 || j as usize >= rows) {
        return crate::FAILED;
    }
    let (x, y, out) = (&x[..n], &y[..n], &mut out[..n]);
    let d = Near { keys, first, last, line, ms, nk: &[], nm: &[], o, base, top, gap, own: false };
    let cl = Colours { now, o };
    let s = Soft { at: at.d, cols, a: a.d, gy, gx, c, fp, ring: ring.d, nl, tstart, ta, tb, cl };
    let nw = Newton { p: p.d, gy: pgy, gx: pgx, kind, lx, ly, bounds: &[], has_b: false, steps, give_step: 0,
                      give_far: 0.0, lazy };
    match crate::pool_for(many, n, 1) {
        None => soft2_range(idx, x, y, &s, &d, &nw, near, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(256);
            pool.install(|| {
                (idx.par_chunks(chunk), x.par_chunks(chunk), y.par_chunks(chunk), out.par_chunks_mut(chunk))
                    .into_par_iter().for_each(|(i, xs, ys, oc)| soft2_range(i, xs, ys, &s, &d, &nw, near, oc));
            });
        }
    }
    0
}

/// recolour._mixed (splitmix64) on one number.
#[inline(always)]
fn mix(x: u64) -> u64 {
    let x = x.wrapping_add(0x9E3779B97F4A7C15);
    let x = (x ^ (x >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
    let x = (x ^ (x >> 27)).wrapping_mul(0x94D049BB133111EB);
    x ^ (x >> 31)
}

/// fastloops.dice_mix -> 0.
#[inline(always)]
pub fn dice_mix(seed: u64, c0: &[u64], c1: &[u64], c2: &[u64], c3: &[u64], out: &mut [f64]) -> i64 {
    let n = out.len();
    let (c0, c1, c2, c3) = (&c0[..n], &c1[..n], &c2[..n], &c3[..n]);
    for i in 0..n {
        let mut x = mix(seed ^ c0[i]);
        x = mix(x ^ c1[i]);
        x = mix(x ^ c2[i]);
        x = mix(x ^ c3[i]);
        out[i] = (x >> 11) as f64 / 9007199254740992.0;
    }
    0
}

/// fastloops.recolour_apart (new changed in place) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn recolour_apart(order: &[i64], ss: &[i64], e: &[i64], old: &[i64], new: &mut [i64], pick: &[f64],
                      bstart: &[i64], bsize: &[i64], nc: i64) -> i64 {
    if apart_plain(order, ss, e, old, new, pick, bstart, bsize, nc) {
        apart_walk::<false>(order, ss, e, old, new, pick, bstart, bsize, nc)
    } else {
        apart_walk::<true>(order, ss, e, old, new, pick, bstart, bsize, nc)
    }
}

/// Every place recolour_apart will read checked once: bursts inside order / ss, notes inside e / old / new / pick,
/// colours (old, new) in 0 .. nc. Then its walk reads without a check a step (Numba checks none either).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn apart_plain(order: &[i64], ss: &[i64], e: &[i64], old: &[i64], new: &[i64], pick: &[f64], bstart: &[i64],
               bsize: &[i64], nc: i64) -> bool {
    let notes = e.len().min(old.len()).min(new.len()).min(pick.len()) as i64;
    let places = order.len().min(ss.len()) as i64;
    if bsize.len() < bstart.len() || nc < 1 {
        return false;
    }
    for w in 0..bstart.len() {
        let (a, z) = (bstart[w], bsize[w]);
        if a < 0 || z < 0 || a + z > places {
            return false;
        }
        for j in a..a + z {
            let i = order[j as usize];
            if i < 0 || i >= notes {
                return false;
            }
            let (o, n) = (old[i as usize], new[i as usize]);
            if o < 0 || o >= nc || n < 0 || n >= nc {
                return false;
            }
        }
    }
    true
}

#[inline(always)]
fn at<T: Copy, const CHECK: bool>(s: &[T], i: usize) -> T {
    if CHECK {
        s[i]
    } else {
        unsafe { *s.get_unchecked(i) }
    }
}

#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn apart_walk<const CHECK: bool>(order: &[i64], ss: &[i64], e: &[i64], old: &[i64], new: &mut [i64], pick: &[f64],
                                 bstart: &[i64], bsize: &[i64], nc: i64) -> i64 {
    let most = if bsize.is_empty() { 1 } else { bsize.iter().copied().max().unwrap_or(1).max(1) };
    let mut act = need!(zeros::<i64>(most as usize));
    let ncu = nc.max(0) as usize;
    let mut taken = need!(zeros::<bool>(ncu));
    for w in 0..bstart.len() {
        let mut na = 0usize;
        for j in bstart[w]..bstart[w] + bsize[w] {
            let ju = j as usize;
            let i = at::<_, CHECK>(order, ju) as usize;
            let sj = at::<_, CHECK>(ss, ju);
            let mut m = 0usize;
            for q in 0..na {
                let x = at::<_, CHECK>(&act, q);
                if at::<_, CHECK>(e, x as usize) > sj {
                    act[m] = x;
                    m += 1;
                }
            }
            na = m;
            taken.iter_mut().for_each(|t| *t = false);
            let mut count = 0i64;
            let oi = at::<_, CHECK>(old, i);
            for q in 0..na {
                let x = at::<_, CHECK>(&act, q) as usize;
                let nx = at::<_, CHECK>(new, x) as usize;
                if at::<_, CHECK>(old, x) != oi && !at::<_, CHECK>(&taken, nx) {
                    taken[nx] = true;
                    count += 1;
                }
            }
            if at::<_, CHECK>(&taken, at::<_, CHECK>(new, i) as usize) {
                let free = nc - count;
                if free == 0 || !at::<_, CHECK>(&taken, oi as usize) {
                    new[i] = old[i];
                } else {
                    let mut r = crate::maths::f2i(pick[i] * free as f64).min(free - 1);
                    for c in 0..ncu {
                        if !taken[c] {
                            if r == 0 {
                                new[i] = c as i64;
                                break;
                            }
                            r -= 1;
                        }
                    }
                }
            }
            act[na] = i as i64;
            na += 1;
        }
    }
    0
}
