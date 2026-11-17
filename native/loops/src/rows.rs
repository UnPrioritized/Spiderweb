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

/// Places sorted by start, NaN last, ties by place (np.lexsort's stable order on one row).
#[inline(always)]
fn by_start(s: &[f64], idx: &mut [usize]) {
    idx.sort_unstable_by(|&x, &y| {
        let (a, b) = (s[x], s[y]);
        let o = match (a.is_nan(), b.is_nan()) {
            (false, false) => a.partial_cmp(&b).unwrap(),
            (true, false) => std::cmp::Ordering::Greater,
            (false, true) => std::cmp::Ordering::Less,
            (true, true) => std::cmp::Ordering::Equal,
        };
        o.then(x.cmp(&y))
    });
}

/// One key row of repeat_ends: its notes' places idx (sorted here), steps = room for its start steps; writes the
/// row's (bw, lo, hi, lo2, hi2) into out[0..5] (from start / end as given). -> true = its lowest / highest middle is
/// a zero: NumPy's reduce picks 0.0 or -0.0 by its own inner order there (the caller then uses the NumPy way).
#[inline(always)]
fn repeat_inner(s: &[f64], e: &[f64], idx: &mut [usize], steps: &mut [f64], start: f64, end: f64, out: &mut [f64])
                -> bool {
    by_start(s, idx);
    let mut last = f64::NEG_INFINITY;
    let mut m = 0usize;
    for j in 0..idx.len() {
        last = npmax(last, s[idx[j]]);
        if j > 0 {
            let st = s[idx[j]] - s[idx[j - 1]];
            if st > 0.0 {
                steps[m] = st;
                m += 1;
            }
        }
    }
    out[0] = end;
    out[1] = start;
    out[2] = end;
    out[3] = start;
    out[4] = end;
    if m == 0 {
        return false;
    }
    let steps = &mut steps[..m];
    steps.sort_unstable_by(|a, b| a.partial_cmp(b).unwrap()); // (all > 0: no NaN)
    let med = steps[(m - 1) / 2];
    out[0] = npmax(end, last + med);
    let (mut m0, mut m1) = (f64::INFINITY, f64::NEG_INFINITY);
    let mut first = true;
    for &i in idx.iter() {
        let mid = (s[i] + e[i]) / 2.0;
        if first {
            m0 = mid;
            m1 = mid;
            first = false;
        } else {
            m0 = npmin(m0, mid);
            m1 = npmax(m1, mid);
        }
    }
    out[1] = m0;
    out[2] = m1;
    out[3] = m0 - med / 2.0;
    out[4] = m1 + med / 2.0;
    m0 == 0.0 || m1 == 0.0
}
level_builds!(fn repeat_l(s: &[f64], e: &[f64], idx: &mut [usize], steps: &mut [f64], start: f64, end: f64,
                          out: &mut [f64]) -> bool => repeat_inner);

/// recolour.repeat_ends: notes' key rows k, starts s, ends e (floats; whole-number starts below 2^53 give the same
/// steps), keys = the rows present (sorted, every k among them), starts / ends = each row's first start / last
/// end -> out (5 x rows: bw, lo, hi, lo2, hi2) -> 0. Rows shared out on half the cores.
#[inline(always)]
pub fn repeat_rows(k: &[i64], s: &[f64], e: &[f64], keys: &[i64], starts: &[f64], ends: &[f64],
                   out: crate::NdMut<f64, 2>, many: bool) -> i64 {
    let (n, nr) = (k.len(), keys.len());
    if s.len() < n || e.len() < n || starts.len() < nr || ends.len() < nr || out.sh[0] != 5 || out.sh[1] != nr
        || out.d.len() < 5 * nr || nr == 0 {
        return FAILED;
    }
    let (k0, k1) = (keys[0], keys[nr - 1]);
    if k1 - k0 >= MOST_SPAN {
        return FAILED;
    }
    let span = (k1 - k0 + 1) as usize;
    let mut row_of: Vec<usize> = match crate::filled(span, usize::MAX) {
        Ok(v) => v,
        Err(e) => return e,
    };
    for (r, &key) in keys.iter().enumerate() {
        if key < k0 || key > k1 || (r > 0 && key <= keys[r - 1]) {
            return FAILED;
        }
        row_of[(key - k0) as usize] = r;
    }
    let mut start: Vec<usize> = match zeros(nr + 1) {
        Ok(v) => v,
        Err(e) => return e,
    };
    for &x in k {
        if x < k0 || x > k1 || row_of[(x - k0) as usize] == usize::MAX {
            return FAILED;
        }
        start[row_of[(x - k0) as usize] + 1] += 1;
    }
    for r in 0..nr {
        start[r + 1] += start[r];
    }
    let (mut idx, mut steps): (Vec<usize>, Vec<f64>) = match (zeros(n), zeros(n)) {
        (Ok(a), Ok(b)) => (a, b),
        _ => return crate::NO_MEMORY,
    };
    let mut at: Vec<usize> = match crate::copied(&start[..nr]) {
        Ok(v) => v,
        Err(e) => return e,
    };
    for (i, &x) in k.iter().enumerate() {
        let r = row_of[(x - k0) as usize];
        idx[at[r]] = i;
        at[r] += 1;
    }
    // (each row: its places, its step room, its 5 numbers; out is written row-wise then laid out 5 x rows)
    let mut res: Vec<f64> = match zeros(nr * 5) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let mut jobs = Vec::new();
    if jobs.try_reserve_exact(nr).is_err() {
        return crate::NO_MEMORY;
    }
    let (mut ri, mut rs, mut ro): (&mut [usize], &mut [f64], &mut [f64]) = (&mut idx, &mut steps, &mut res);
    for r in 0..nr {
        let c = start[r + 1] - start[r];
        let (a, b) = std::mem::take(&mut ri).split_at_mut(c);
        let (sa, sb) = std::mem::take(&mut rs).split_at_mut(c);
        let (oa, ob) = std::mem::take(&mut ro).split_at_mut(5);
        jobs.push((r, a, sa, oa));
        ri = b;
        rs = sb;
        ro = ob;
    }
    let (s, e) = (&s[..n], &e[..n]);
    let zero = match pool_for(many, n, 1) {
        None => {
            let mut z = false;
            for (r, a, sa, oa) in jobs {
                z |= repeat_l(s, e, a, sa, starts[r], ends[r], oa);
            }
            z
        }
        Some(pool) => pool.install(|| {
            jobs.into_par_iter().map(|(r, a, sa, oa)| repeat_l(s, e, a, sa, starts[r], ends[r], oa)).reduce(|| false, |x, y| x || y)
        }),
    };
    if zero {
        return FAILED;
    }
    for r in 0..nr {
        for c in 0..5 {
            out.d[c * nr + r] = res[r * 5 + c];
        }
    }
    0
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
