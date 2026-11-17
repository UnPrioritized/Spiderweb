//! Stir's key rows (notes/recolour.py Rows; NOTES ON SEVERAL CORES stage E, 2026-11-18): each key row's first start /
//! last end / shortest half (row_bounds: np.unique + np.minimum.at / maximum.at) and keys past the rows wrapped
//! round (key_wrap: Rows.key_wrapped). The very same numbers as the NumPy way (np.minimum.at keeps the LAST of
//! equal values, -0.0 / 0.0 included, and a NaN once met); on half the cores. Odd input (keys over a span of
//! MOST_SPAN) = FAILED: the NumPy way does that call.

use crate::maths::npmod;
use crate::pool_for;
use crate::pyext::{Out, Outs};
use crate::{filled, zeros, FAILED};
use rayon::prelude::*;

/// Keys spread wider than this = the NumPy way (a table per key value).
const MOST_SPAN: i64 = 1 << 20;

/// np.minimum(cur, x) as ufunc.at applies it: cur when it's smaller or NaN, else x (equal: x, the later one).
#[inline(always)]
fn npmin(cur: f64, x: f64) -> f64 {
    if cur < x || cur.is_nan() {
        cur
    } else {
        x
    }
}

/// np.maximum(cur, x) the same way.
#[inline(always)]
fn npmax(cur: f64, x: f64) -> f64 {
    if cur > x || cur.is_nan() {
        cur
    } else {
        x
    }
}

/// One run of notes into tables (one slot per key value from k0): seen, min a, max b, min d.
#[inline(always)]
fn bounds_inner(k: &[i64], a: &[f64], b: &[f64], d: &[f64], k0: i64, seen: &mut [u8], lo: &mut [f64], hi: &mut [f64],
                dm: &mut [f64]) {
    for i in 0..k.len() {
        let r = (k[i] - k0) as usize;
        seen[r] = 1;
        lo[r] = npmin(lo[r], a[i]);
        hi[r] = npmax(hi[r], b[i]);
        dm[r] = npmin(dm[r], d[i]);
    }
}
level_builds!(fn bounds_l(k: &[i64], a: &[f64], b: &[f64], d: &[f64], k0: i64, seen: &mut [u8], lo: &mut [f64],
                          hi: &mut [f64], dm: &mut [f64]) => bounds_inner);

/// recolour.Rows' bounds: notes' key rows k, starts a, ends b, halves d -> (keys present, sorted; each one's
/// np.minimum.at of a, np.maximum.at of b, np.minimum.at of d, from +inf / -inf / +inf).
#[inline(always)]
pub fn row_bounds(k: &[i64], a: &[f64], b: &[f64], d: &[f64], many: bool) -> Outs {
    let n = k.len();
    if a.len() < n || b.len() < n || d.len() < n {
        return Err(FAILED);
    }
    let (a, b, d) = (&a[..n], &b[..n], &d[..n]);
    if n == 0 {
        return Ok(vec![Out::I64(Vec::new(), 0), Out::F64(Vec::new(), 0), Out::F64(Vec::new(), 0),
                       Out::F64(Vec::new(), 0)]);
    }
    let (mut k0, mut k1) = (k[0], k[0]);
    for &x in k {
        k0 = k0.min(x);
        k1 = k1.max(x);
    }
    if k1 - k0 >= MOST_SPAN {
        return Err(FAILED);
    }
    let span = (k1 - k0 + 1) as usize;
    let mut seen: Vec<u8> = zeros(span)?;
    let mut lo = filled(span, f64::INFINITY)?;
    let mut hi = filled(span, f64::NEG_INFINITY)?;
    let mut dm = filled(span, f64::INFINITY)?;
    match pool_for(many, n, 1) {
        None => bounds_l(k, a, b, d, k0, &mut seen, &mut lo, &mut hi, &mut dm),
        Some(pool) => {
            // (a table set per run, then joined in run order: the same as one pass, ties and NaN too)
            let parts = pool.current_num_threads();
            let per = (n + parts - 1) / parts;
            let mut tabs = Vec::new();
            tabs.try_reserve_exact(parts).map_err(|_| crate::NO_MEMORY)?;
            for _ in 0..parts {
                tabs.push((zeros::<u8>(span)?, filled(span, f64::INFINITY)?, filled(span, f64::NEG_INFINITY)?,
                           filled(span, f64::INFINITY)?));
            }
            pool.install(|| {
                tabs.par_iter_mut().enumerate().for_each(|(p, (s, l, h, m))| {
                    let (x, z) = ((p * per).min(n), ((p + 1) * per).min(n));
                    bounds_l(&k[x..z], &a[x..z], &b[x..z], &d[x..z], k0, s, l, h, m);
                })
            });
            for (s, l, h, m) in &tabs {
                for r in 0..span {
                    if s[r] != 0 {
                        seen[r] = 1;
                        lo[r] = npmin(lo[r], l[r]);
                        hi[r] = npmax(hi[r], h[r]);
                        dm[r] = npmin(dm[r], m[r]);
                    }
                }
            }
        }
    }
    let m = seen.iter().filter(|&&s| s != 0).count();
    let (mut keys, mut ol, mut oh, mut od) = (zeros::<i64>(m)?, zeros::<f64>(m)?, zeros::<f64>(m)?, zeros::<f64>(m)?);
    let mut j = 0;
    for r in 0..span {
        if seen[r] != 0 {
            keys[j] = k0 + r as i64;
            ol[j] = lo[r];
            oh[j] = hi[r];
            od[j] = dm[r];
            j += 1;
        }
    }
    Ok(vec![Out::I64(keys, 0), Out::F64(ol, 0), Out::F64(oh, 0), Out::F64(od, 0)])
}

/// A pointer the pool's threads share (unique_keep: each group writes only its own rows' places).
struct Raw<T>(*mut T);
unsafe impl<T> Send for Raw<T> {}
unsafe impl<T> Sync for Raw<T> {}
impl<T> Raw<T> {
    #[inline(always)]
    fn get(&self) -> *mut T {
        self.0
    }
}

/// One key group (its rows' places, earliest first): sorted by every other column then place, and each row the
/// same as the one before it in every column marked 0 in keep (the earliest copy stays).
#[inline(always)]
fn group_inner(d: &[i64], nc: usize, kc: usize, idx: &mut [usize], keep: &Raw<u8>) {
    let same = |x: usize, y: usize| (0..nc).all(|c| c == kc || d[x * nc + c] == d[y * nc + c]);
    idx.sort_unstable_by(|&x, &y| {
        for c in 0..nc {
            if c != kc {
                let o = d[x * nc + c].cmp(&d[y * nc + c]);
                if o != std::cmp::Ordering::Equal {
                    return o;
                }
            }
        }
        x.cmp(&y)
    });
    for j in 1..idx.len() {
        if same(idx[j - 1], idx[j]) {
            // (each row is in one group only: no two threads write one place)
            unsafe { *keep.get().add(idx[j]) = 0 };
        }
    }
}
level_builds!(fn group_l(d: &[i64], nc: usize, kc: usize, idx: &mut [usize], keep: &Raw<u8>) => group_inner);

/// engine.unique_index for whole-number note rows (a: n x columns, kc = the key column): keep[i] = 1 unless row i is
/// the same as an earlier row in every column -> how many are kept. Rows grouped by key (a counting pass), each
/// group sorted on its own (groups shared out on half the cores). FAILED: keys over a span of MOST_SPAN.
#[inline(always)]
pub fn unique_keep(a: crate::Nd<i64, 2>, kc: i64, keep: &mut [u8], many: bool) -> i64 {
    let (n, nc) = (a.sh[0], a.sh[1]);
    let kc = kc as usize;
    if kc >= nc || keep.len() < n || a.d.len() < n * nc {
        return FAILED;
    }
    if n == 0 {
        return 0;
    }
    let d = &a.d[..n * nc];
    let (mut k0, mut k1) = (d[kc], d[kc]);
    for i in 0..n {
        let k = d[i * nc + kc];
        k0 = k0.min(k);
        k1 = k1.max(k);
    }
    if k1 - k0 >= MOST_SPAN {
        return FAILED;
    }
    let span = (k1 - k0 + 1) as usize;
    let mut start: Vec<usize> = match zeros(span + 1) {
        Ok(v) => v,
        Err(e) => return e,
    };
    for i in 0..n {
        start[(d[i * nc + kc] - k0) as usize + 1] += 1;
    }
    for r in 0..span {
        start[r + 1] += start[r];
    }
    let mut idx: Vec<usize> = match zeros(n) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let mut at: Vec<usize> = match crate::copied(&start[..span]) {
        Ok(v) => v,
        Err(e) => return e,
    };
    for i in 0..n {
        let r = (d[i * nc + kc] - k0) as usize;
        idx[at[r]] = i;
        at[r] += 1;
    }
    let keep = &mut keep[..n];
    keep.fill(1);
    let raw = Raw(keep.as_mut_ptr());
    // (the groups as separate pieces of idx)
    let mut groups = Vec::new();
    if groups.try_reserve_exact(span).is_err() {
        return crate::NO_MEMORY;
    }
    let mut rest: &mut [usize] = &mut idx;
    for r in 0..span {
        let (g, more) = std::mem::take(&mut rest).split_at_mut(start[r + 1] - start[r]);
        if g.len() > 1 {
            groups.push(g);
        }
        rest = more;
    }
    match pool_for(many, n, 1) {
        None => {
            for g in groups {
                group_l(d, nc, kc, g, &raw);
            }
        }
        Some(pool) => pool.install(|| groups.into_par_iter().for_each(|g| group_l(d, nc, kc, g, &raw))),
    }
    keep.iter().map(|&x| x as i64).sum()
}

#[inline(always)]
fn wrap_inner(key: &[f64], base: f64, h: f64, out: &mut [f64]) {
    for (o, &x) in out.iter_mut().zip(key) {
        *o = if x < base || x >= base + h { base + npmod(x - base, h) } else { x };
    }
}
level_builds!(fn wrap_l(key: &[f64], base: f64, h: f64, out: &mut [f64]) => wrap_inner);

/// recolour.Rows.key_wrapped: np.where((key < base) | (key >= base + h), base + np.mod(key - base, h), key) into
/// out -> 0.
#[inline(always)]
pub fn key_wrap(key: &[f64], base: f64, h: f64, out: &mut [f64], many: bool) -> i64 {
    let n = key.len();
    if out.len() < n {
        return FAILED;
    }
    let out = &mut out[..n];
    match pool_for(many, n, 2) {
        None => wrap_l(key, base, h, out),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 4)).max(4096);
            pool.install(|| {
                key.par_chunks(chunk).zip(out.par_chunks_mut(chunk)).for_each(|(kc, oc)| wrap_l(kc, base, h, oc))
            });
        }
    }
    0
}
