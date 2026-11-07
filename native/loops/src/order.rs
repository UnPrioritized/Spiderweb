//! np.lexsort by key, then up to 3 more columns: fastloops.key_order's twin (rows grouped by key with a counting
//! pass, then each group's runs already in order merged). Every row number inside comes from the counting pass
//! (0 .. n) and the columns are cut to n first, so nothing is checked again inside.
#![allow(unused_unsafe)] // (at!(a, at!(b, i)): the inner one is inside the outer unsafe already)
use rayon::prelude::*;

use crate::{try_zeros, NO_MEMORY};

macro_rules! at {
    ($a:expr, $i:expr) => {
        *unsafe { $a.get_unchecked($i as usize) }
    };
}
macro_rules! put {
    ($a:expr, $i:expr, $v:expr) => {
        *unsafe { $a.get_unchecked_mut($i as usize) } = $v
    };
}

/// Row i before row j by (a, b, c)?
#[inline(always)]
fn before(i: i64, j: i64, a: &[i64], b: &[i64], c: &[i64]) -> bool {
    let (ai, aj) = (at!(a, i), at!(a, j));
    if ai != aj {
        return ai < aj;
    }
    let (bi, bj) = (at!(b, i), at!(b, j));
    if bi != bj {
        return bi < bj;
    }
    at!(c, i) < at!(c, j)
}

/// idx sorted by (a, b, c), rows alike kept in their order: its runs already in order merged in pairs.
/// The scratch lists (bounds / nb: the runs' edges, tmp: merging) are made only when idx isn't in order already
/// (rows mostly come in order: none), `want` long (the biggest group's length) at least. -> false = no memory.
#[inline(always)]
fn runs_sort(idx: &mut [i64], a: &[i64], b: &[i64], c: &[i64], want: usize, tmp: &mut Vec<i64>,
             bounds: &mut Vec<usize>, nb: &mut Vec<usize>) -> bool {
    let n = idx.len();
    let mut i = 1;
    while i < n && !before(at!(idx, i), at!(idx, i - 1), a, b, c) {
        i += 1;
    }
    if i >= n {
        return true; // (in order)
    }
    if tmp.len() < n {
        match (try_zeros::<i64>(want.max(n)), try_zeros::<usize>(want.max(n) + 2), try_zeros::<usize>(want.max(n) + 2)) {
            (Some(t), Some(b0), Some(b1)) => (*tmp, *bounds, *nb) = (t, b0, b1),
            _ => return false,
        }
    }
    let (tmp, bounds, nb) = (&mut tmp[..n], &mut bounds[..n + 2], &mut nb[..n + 2]);
    let mut nbo = 1;
    put!(bounds, 0, 0);
    while i < n {
        if before(at!(idx, i), at!(idx, i - 1), a, b, c) {
            put!(bounds, nbo, i);
            nbo += 1;
        }
        i += 1;
    }
    put!(bounds, nbo, n);
    nbo += 1;
    let (mut bounds, mut nb) = (bounds, nb);
    while nbo > 2 {
        let mut nn = 1;
        put!(nb, 0, 0);
        let mut r = 0;
        while r < nbo - 1 {
            let lo = at!(bounds, r);
            if r + 2 >= nbo {
                put!(nb, nn, at!(bounds, r + 1));
                nn += 1;
                r += 2;
                continue;
            }
            let (mid, hi) = (at!(bounds, r + 1), at!(bounds, r + 2));
            let (mut p, mut q, mut m) = (lo, mid, lo);
            while p < mid && q < hi {
                let (vq, vp) = (at!(idx, q), at!(idx, p));
                if before(vq, vp, a, b, c) {
                    put!(tmp, m, vq);
                    q += 1;
                } else {
                    put!(tmp, m, vp);
                    p += 1;
                }
                m += 1;
            }
            while p < mid {
                put!(tmp, m, at!(idx, p));
                p += 1;
                m += 1;
            }
            while q < hi {
                put!(tmp, m, at!(idx, q));
                q += 1;
                m += 1;
            }
            idx[lo..hi].copy_from_slice(&tmp[lo..hi]);
            put!(nb, nn, hi);
            nn += 1;
            r += 2;
        }
        std::mem::swap(&mut bounds, &mut nb);
        nbo = nn;
    }
    true
}

/// Groups g0 .. g1 sorted; order starts at row `base` (at[g] - base = the group's place in it). -> false = no
/// memory.
#[inline(always)]
fn groups_inner(order: &mut [i64], at: &[usize], base: usize, g0: usize, g1: usize, a: &[i64], b: &[i64], c: &[i64])
                -> bool {
    let want = (g0..g1).map(|g| at[g + 1] - at[g]).max().unwrap_or(0);
    let (mut tmp, mut bounds, mut nb) = (Vec::new(), Vec::new(), Vec::new());
    for g in g0..g1 {
        let (lo, hi) = (at[g] - base, at[g + 1] - base);
        if hi - lo > 1 && !runs_sort(&mut order[lo..hi], a, b, c, want, &mut tmp, &mut bounds, &mut nb) {
            return false;
        }
    }
    true
}

level_builds!(fn groups(order: &mut [i64], at: &[usize], base: usize, g0: usize, g1: usize, a: &[i64], b: &[i64],
                      c: &[i64]) -> bool => groups_inner);

/// The counting pass: rows by key, lowest first, in their order -> at (group g = order[at[g] .. at[g + 1]]).
#[inline(always)]
fn bucket_inner(key: &[i64], order: &mut [i64], at: &mut [usize], pos: &mut [usize], kmin: i64) {
    let nk = pos.len();
    for &k in key {
        let g = (k - kmin) as usize + 1;
        put!(at, g, at!(at, g) + 1);
    }
    for g in 0..nk {
        put!(at, g + 1, at!(at, g + 1) + at!(at, g));
    }
    pos.copy_from_slice(&at[..nk]);
    for (i, &k) in key.iter().enumerate() {
        let g = (k - kmin) as usize;
        let p = at!(pos, g);
        put!(order, p, i as i64);
        put!(pos, g, p + 1);
    }
}

level_builds!(fn bucket(key: &[i64], order: &mut [i64], at: &mut [usize], pos: &mut [usize], kmin: i64) => bucket_inner);

/// The counting pass shared out, step 1: how many rows of each key in one chunk of the rows.
#[inline(always)]
fn count_inner(key: &[i64], counts: &mut [usize], kmin: i64) {
    for &k in key {
        let g = (k - kmin) as usize;
        put!(counts, g, at!(counts, g) + 1);
    }
}

level_builds!(fn count(key: &[i64], counts: &mut [usize], kmin: i64) => count_inner);

/// Where a chunk writes its rows (raw: chunks write to places no other chunk writes to).
#[derive(Clone, Copy)]
struct Out(*mut i64);
unsafe impl Send for Out {}
unsafe impl Sync for Out {}

/// Step 2: a chunk's rows (from row `first`) put in their places; pos = where its next row of each key goes.
#[inline(always)]
fn scatter_inner(key: &[i64], first: usize, pos: &mut [usize], out: Out, kmin: i64) {
    for (i, &k) in key.iter().enumerate() {
        let g = (k - kmin) as usize;
        let p = at!(pos, g);
        unsafe { *out.0.add(p) = (first + i) as i64 };
        put!(pos, g, p + 1);
    }
}

level_builds!(fn scatter(key: &[i64], first: usize, pos: &mut [usize], out: Out, kmin: i64) => scatter_inner);

/// The counting pass on the pool's threads (run inside its install): chunk j's rows of a key go after chunks
/// 0 .. j-1's (the same order as one core). -> at, or None = no memory.
fn bucket_many(t: usize, key: &[i64], order: &mut [i64], nk: usize, kmin: i64) -> Option<Vec<usize>> {
    let n = key.len();
    let chunk = n.div_ceil(t);
    let parts = n.div_ceil(chunk);
    let mut counts = try_zeros::<usize>(parts * nk)?;
    key.par_chunks(chunk).zip(counts.par_chunks_mut(nk)).for_each(|(kc, cc)| count(kc, cc, kmin));
    let mut at = try_zeros::<usize>(nk + 1)?;
    let mut run = 0;
    for g in 0..nk {
        at[g] = run;
        for j in 0..parts {
            let c = counts[j * nk + g];
            counts[j * nk + g] = run; // (now: where chunk j's first row of key g goes)
            run += c;
        }
    }
    at[nk] = run;
    let out = Out(order.as_mut_ptr());
    key.par_chunks(chunk).zip(counts.par_chunks_mut(nk)).enumerate()
        .for_each(|(j, (kc, pc))| scatter(kc, j * chunk, pc, out, kmin));
    Some(at)
}

/// key_order on the pool's t threads (run inside ONE install: entering the pool is the costly part, ~25 us).
fn key_order_many(t: usize, key: &[i64], a: &[i64], b: &[i64], c: &[i64], order: &mut [i64], nk: usize, kmin: i64)
                  -> i64 {
    let n = key.len();
    let at = if nk.saturating_mul(t) <= n {
        bucket_many(t, key, order, nk, kmin)
    } else {
        // (a wide key range: a count per key for every thread would cost more than it saves)
        let (Some(mut at), Some(mut pos)) = (try_zeros::<usize>(nk + 1), try_zeros::<usize>(nk)) else {
            return NO_MEMORY;
        };
        bucket(key, order, &mut at, &mut pos, kmin);
        Some(at)
    };
    let Some(at) = at else {
        return NO_MEMORY;
    };
    // (whole groups in spans of about n / (threads x 4) rows each, sorted side by side)
    let spans_most = t * 4;
    let mut cuts = vec![0usize];
    for g in 1..nk {
        if at[g] >= n * cuts.len() / spans_most && at[g] > at[*cuts.last().unwrap()] {
            cuts.push(g);
        }
    }
    cuts.push(nk);
    let mut work = Vec::with_capacity(cuts.len());
    let mut rest: &mut [i64] = order;
    for w in cuts.windows(2) {
        let (g0, g1) = (w[0], w[1]);
        let (base, hi) = (at[g0], at[g1]);
        let (oa, ob) = std::mem::take(&mut rest).split_at_mut(hi - base);
        rest = ob;
        work.push((oa, base, g0, g1));
    }
    let at = &at;
    if work.into_par_iter().all(|(oa, base, g0, g1)| groups(oa, at, base, g0, g1, a, b, c)) {
        0
    } else {
        NO_MEMORY
    }
}

/// np.lexsort((c, b, a, key)) into order; many = the key groups shared out over the pool (each group on its own:
/// the same order). -> 0.
pub fn key_order(key: &[i64], a: &[i64], b: &[i64], c: &[i64], order: &mut [i64], many: bool) -> i64 {
    let n = key.len();
    if n == 0 {
        return 0;
    }
    let (a, b, c, order) = (&a[..n], &b[..n], &c[..n], &mut order[..n]); // (too short = a panic, caught)
    let (kmin, kmax) = key.iter().fold((i64::MAX, i64::MIN), |(lo, hi), &k| (lo.min(k), hi.max(k)));
    let Some(nk) = kmax.checked_sub(kmin).and_then(|d| usize::try_from(d).ok()).and_then(|d| d.checked_add(1)) else {
        return NO_MEMORY;
    };
    // (from 2 x LEAST rows: one core is quicker below, measured 2026-11-08 dev/scratch/native_keycores.py)
    let Some(pool) = crate::pool_for(many, n, 2) else {
        let (Some(mut at), Some(mut pos)) = (try_zeros::<usize>(nk + 1), try_zeros::<usize>(nk)) else {
            return NO_MEMORY;
        };
        bucket(key, order, &mut at, &mut pos, kmin);
        drop(pos);
        return if groups(order, &at, 0, 0, nk, a, b, c) { 0 } else { NO_MEMORY };
    };
    let t = pool.current_num_threads();
    pool.install(|| key_order_many(t, key, a, b, c, order, nk, kmin))
}
