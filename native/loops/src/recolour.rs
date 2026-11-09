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

/// The numbers warp_newton reads past the spots.
struct Newton<'a> {
    p: &'a [f64],
    gy: usize,
    gx: usize,
    smooth: bool,
    bounds: &'a [f64],
    has_b: bool,
    steps: i64,
    give_step: i64,
    give_far: f64,
}

/// fastloops._newton_one for spot i (px / py / sx / sy its own numbers): (x, y, how far off).
#[inline(always)]
fn newton_one(i: usize, px: f64, py: f64, sx: f64, sy: f64, d: &Newton) -> (f64, f64, f64) {
    let (mut x, mut y) = (sx, sy);
    let mut b = f64::INFINITY;
    let (mut ox, mut oy) = (x, y);
    let h = 1e-5;
    for step in 0..d.steps {
        let (vx, vy, mut dxx, mut dxy, mut dyx, mut dyy) = if d.smooth {
            let e = eased_at(d.p, d.gy, d.gx, x, y);
            (e[0], e[1], e[2], e[3], e[4], e[5])
        } else {
            let (vx, vy) = sample2(d.p, d.gy, d.gx, x, y);
            (vx, vy, 0.0, 0.0, 0.0, 0.0)
        };
        let (rx, ry) = (x + vx - px, y + vy - py);
        let err = (rx * rx + ry * ry).sqrt();
        if err < b {
            (b, ox, oy) = (err, x, y);
        }
        let mut go = err > 1e-9;
        if d.has_b && step == d.give_step {
            go = go && err < d.give_far;
        }
        if !go {
            break;
        }
        if !d.smooth {
            let (ax, ay) = sample2(d.p, d.gy, d.gx, x + h, y);
            (dxx, dxy) = ((ax - vx) / h, (ay - vy) / h);
            let (ax, ay) = sample2(d.p, d.gy, d.gx, x, y + h);
            (dyx, dyy) = ((ax - vx) / h, (ay - vy) / h);
        }
        let (a, bb, c, e) = (1.0 + dxx, dyx, dxy, 1.0 + dyy);
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
        (x, y) = (nx, ny);
    }
    (ox, oy, b)
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

/// fastloops.warp_newton (P: the offsets (gy, gx, 2); bounds (n, 4) read when has_b; many = shared out) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn warp_newton(p: Nd<f64, 3>, px: &[f64], py: &[f64], smooth: bool, sx: &[f64], sy: &[f64], bounds: Nd<f64, 2>,
                   has_b: bool, steps: i64, give_step: i64, give_far: f64, bx: &mut [f64], by: &mut [f64],
                   best: &mut [f64], many: bool) -> i64 {
    let n = px.len();
    let (gy, gx) = (p.sh[0], p.sh[1]);
    if p.sh[2] != 2 || gy < 2 || gx < 2 || (has_b && (bounds.sh[1] != 4 || bounds.sh[0] < n)) {
        return crate::FAILED;
    }
    let (py, sx, sy) = (&py[..n], &sx[..n], &sy[..n]);
    let (bx, by, best) = (&mut bx[..n], &mut by[..n], &mut best[..n]);
    let d = Newton { p: p.d, gy, gx, smooth, bounds: bounds.d, has_b, steps, give_step, give_far };
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

/// fastloops._unit_back: a spot of the colour box -> (middle in ticks, key) (liquify._placed + from_unit).
#[inline(always)]
fn unit_back(mut x: f64, mut y: f64, fp: &[f64]) -> (f64, f64) {
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
    if fp[1] != 0.0 {
        let x2 = fp[2] + x * fp[3];
        let y2 = if fp[6] != 0.0 { fp[4] + y * fp[5] } else { y };
        (x, y) = (0.5 + fp[8] * (y2 - 0.5), 0.5 + fp[7] * (x2 - 0.5));
    }
    (fp[9] + x * fp[10], if fp[13] != 0.0 { fp[11] + y * fp[12] } else { fp[11] })
}

const ROUND_INNER: usize = 7; // (fastloops.ROUND_INNER: look 0 the middle, 1 .. 6 the inner ring)
const MOST_LOOKS: usize = 16;

/// fastloops._round_at: a stretched spot's looks round where it fetches from (sx, sy) vote.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn round_one(sx: f64, sy: f64, r: f64, ring: &[f64], nl: usize, fp: &[f64], d: &Near, now: &[i64]) -> i64 {
    let mut got = [0i64; MOST_LOOKS];
    let look = |k: usize| {
        let (m, kk) = unit_back(sx + ring[k * 3] * r, sy + ring[k * 3 + 1] * r, fp);
        near_one(0, m, kk, d)
    };
    got[0] = look(0);
    for k in ROUND_INNER..nl {
        got[k] = look(k);
    }
    let c0 = now[got[0] as usize];
    if (ROUND_INNER..nl).all(|k| now[got[k] as usize] == c0) {
        return got[0];
    }
    for k in 1..ROUND_INNER {
        got[k] = look(k);
    }
    let mut cols = [0i64; MOST_LOOKS];
    let mut score = [0.0f64; MOST_LOOKS];
    let mut nc = 0usize;
    for k in 0..nl {
        let c = now[got[k] as usize];
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
        if now[got[k] as usize] == cols[best] {
            return got[k];
        }
    }
    got[0]
}

/// fastloops._cover_at: a squashed note's nt x nk looks (tstart / ta / tb) vote, the most common colour wins.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn cover_one(mid: f64, key: f64, vt: (f64, f64), vk: (f64, f64), nt: i64, nk: i64, tstart: &[i64], ta: &[f64],
             tb: &[f64], d: &Near, now: &[i64]) -> i64 {
    let t = ((nt - 1) * 4 + (nk - 1)) as usize;
    let (a0, a1) = (tstart[t] as usize, tstart[t + 1] as usize);
    let s = a1 - a0;
    let mut got = [0i64; MOST_LOOKS];
    for j in 0..s {
        let pm = mid + ta[a0 + j] * vt.0 + tb[a0 + j] * vk.0;
        let pk = key + ta[a0 + j] * vt.1 + tb[a0 + j] * vk.1;
        got[j] = near_one(0, pm, pk, d);
    }
    if s == 1 {
        return got[0];
    }
    let mut cols = [0i64; MOST_LOOKS];
    let mut count = [0i64; MOST_LOOKS];
    let mut firstj = [0i64; MOST_LOOKS];
    let mut nc = 0usize;
    for j in 0..s {
        let c = now[got[j] as usize];
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
    got[firstj[best] as usize]
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
    now: &'a [i64],
}

/// fastloops._soft_one: note j at spot (x0, y0) of the box -> the note it takes its colour from.
#[inline(always)]
fn soft_one(j: usize, x0: f64, y0: f64, s: &Soft, d: &Near) -> i64 {
    let s100 = s.c[5];
    let (d0, d1) = sample2(s.a, s.gy, s.gx, x0, y0);
    let (sx, sy) = (x0 + d0 * s100, y0 + d1 * s100);
    let (mid, key) = unit_back(sx, sy, s.fp);
    let (st, en) = (s.at[j * s.cols] as f64, s.at[j * s.cols + 1] as f64);
    let h = pmax((en - st) / 2.0, 0.5);
    let (xx, yy) = (x0 + s.c[0] * h, y0 + s.c[1] * h);
    let (d0, d1) = sample2(s.a, s.gy, s.gx, xx, yy);
    let (m1, k1) = unit_back(xx + d0 * s100, yy + d1 * s100, s.fp);
    let (xx, yy) = (x0 + s.c[2], y0 + s.c[3]);
    let (d0, d1) = sample2(s.a, s.gy, s.gx, xx, yy);
    let (m3, k3) = unit_back(xx + d0 * s100, yy + d1 * s100, s.fp);
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
        return round_one(sx, sy, s.c[4] * s.c[7] * grow, s.ring, s.nl, s.fp, d, s.now);
    }
    let nt = pmin(pmax((ut * ut + uk * uk).sqrt().ceil(), 1.0), 4.0) as i64;
    let nk = pmin(pmax((wt * wt + wk * wk).sqrt().ceil(), 1.0), 4.0) as i64;
    cover_one(mid, key, (vt0, vt1), (vk0, vk1), nt, nk, s.tstart, s.ta, s.tb, d, s.now)
}

#[inline(always)]
fn soft_range_inner(idx: &[i64], x: &[f64], y: &[f64], s: &Soft, d: &Near, out: &mut [i64]) {
    for j in 0..out.len() {
        out[j] = soft_one(idx[j] as usize, x[j], y[j], s, d);
    }
}
level_builds!(fn soft_range(idx: &[i64], x: &[f64], y: &[f64], s: &Soft, d: &Near, out: &mut [i64])
              => soft_range_inner);

/// fastloops.warp_soft (at: the notes' places; idx: the notes picked for; a: the map (gy, gx, 2); c / fp: the
/// numbers _soft_one / _unit_back read; ring (looks, 3); many = shared out) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn warp_soft(at: Nd<i64, 2>, idx: &[i64], x: &[f64], y: &[f64], a: Nd<f64, 3>, c: &[f64], fp: &[f64],
                 ring: Nd<f64, 2>, tstart: &[i64], ta: &[f64], tb: &[f64], keys: &[i64], first: &[i64],
                 last: &[i64], line: &[f64], ms: &[f64], o: &[i64], base: f64, top: f64, gap: f64, now: &[i64],
                 out: &mut [i64], many: bool) -> i64 {
    let n = idx.len();
    let (rows, cols) = (at.sh[0], at.sh[1]);
    let (gy, gx) = (a.sh[0], a.sh[1]);
    let nl = ring.sh[0];
    if cols < 2 || a.sh[2] != 2 || gy < 2 || gx < 2 || ring.sh[1] != 3 || nl > MOST_LOOKS || nl < ROUND_INNER
        || c.len() < 8 || fp.len() < 14 || keys.is_empty() || tstart.len() < 17 || x.len() < n || y.len() < n
        || out.len() < n {
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
    let s = Soft { at: at.d, cols, a: a.d, gy, gx, c, fp, ring: ring.d, nl, tstart, ta, tb, now };
    match crate::pool_for(many, n, 1) {
        None => soft_range(idx, x, y, &s, &d, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(256);
            pool.install(|| {
                (idx.par_chunks(chunk), x.par_chunks(chunk), y.par_chunks(chunk), out.par_chunks_mut(chunk))
                    .into_par_iter().for_each(|(i, xs, ys, oc)| soft_range(i, xs, ys, &s, &d, oc));
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
