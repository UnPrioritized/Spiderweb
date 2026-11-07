//! Recolour / Stir / Liquify's helpers (notes/recolour.py, liquify.py): fastloops' stir_nearest, stir_wrap,
//! liquify_sample (each spot on its own: shared out over the pool past LEAST spots, the very same numbers),
//! dice_mix, recolour_apart. The same sums in the same order as those.
use rayon::prelude::*;

use crate::maths::{npmod, pmax, pmin, rint};
use crate::{Nd, NdMut};

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

/// fastloops._near_one for spot s (mid m, key k).
#[inline(always)]
fn near_one(s: usize, m: f64, k: f64, d: &Near) -> i64 {
    let q = rint(k);
    let row = nearest_row(d.keys, q);
    let (lo, hi) = (d.first[row], d.last[row]);
    let c = pmin(pmax(m, d.base), d.top);
    let v = c - d.base + row as f64 * d.gap;
    let (mut a, mut b) = (lo, hi);
    while a < b {
        let h = (a + b) >> 1;
        if d.line[wrap(h, d.line.len())] < v {
            a = h + 1;
        } else {
            b = h;
        }
    }
    let mut p = a.max(lo).min(hi - 1);
    let p0 = (p - 1).max(lo);
    let nms = d.ms.len();
    if (d.ms[wrap(p0, nms)] - m).abs() <= (d.ms[wrap(p, nms)] - m).abs() {
        p = p0;
    }
    let mut got = d.o[wrap(p, d.o.len())];
    if d.own && d.nk[s] == d.keys[row] && (d.nm[s] - m).abs() <= (d.ms[wrap(p, nms)] - m).abs() + 1e-9 {
        got = s as i64;
    }
    got
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
    for (j, o) in out.iter_mut().enumerate() {
        *o = wrap_one(mid[j], key[j], keys, a, b);
    }
}
level_builds!(fn wrap_range(keys: &[i64], a: &[f64], b: &[f64], mid: &[f64], key: &[f64], out: &mut [f64])
              => wrap_range_inner);

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
    let (row, col) = (gx * 2, 2usize);
    for i in 0..x.len() {
        let fx = pmin(pmax(x[i], 0.0), 1.0) * (gx as i64 - 1) as f64;
        let fy = pmin(pmax(y[i], 0.0), 1.0) * (gy as i64 - 1) as f64;
        let i0 = (fx as i64).min(gx as i64 - 2);
        let j0 = (fy as i64).min(gy as i64 - 2);
        let (tx, ty) = (fx - i0 as f64, fy - j0 as f64);
        let (i0, j0) = (i0 as usize, j0 as usize);
        for c in 0..2 {
            let at = |j: usize, i: usize| a[j * row + i * col + c];
            out[i * 2 + c] = (at(j0, i0) * (1.0 - tx) + at(j0, i0 + 1) * tx) * (1.0 - ty)
                + (at(j0 + 1, i0) * (1.0 - tx) + at(j0 + 1, i0 + 1) * tx) * ty;
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
    let most = if bsize.is_empty() { 1 } else { bsize.iter().copied().max().unwrap_or(1).max(1) };
    let mut act = vec![0i64; most as usize];
    let ncu = nc.max(0) as usize;
    let mut taken = vec![false; ncu];
    for w in 0..bstart.len() {
        let mut na = 0usize;
        for j in bstart[w]..bstart[w] + bsize[w] {
            let ju = j as usize;
            let i = order[ju] as usize;
            let mut m = 0usize;
            for q in 0..na {
                let x = act[q];
                if e[x as usize] > ss[ju] {
                    act[m] = x;
                    m += 1;
                }
            }
            na = m;
            taken.iter_mut().for_each(|t| *t = false);
            let mut count = 0i64;
            for q in 0..na {
                let x = act[q] as usize;
                if old[x] != old[i] && !taken[new[x] as usize] {
                    taken[new[x] as usize] = true;
                    count += 1;
                }
            }
            if taken[new[i] as usize] {
                let free = nc - count;
                if free == 0 || !taken[old[i] as usize] {
                    new[i] = old[i];
                } else {
                    let mut r = ((pick[i] * free as f64) as i64).min(free - 1);
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
