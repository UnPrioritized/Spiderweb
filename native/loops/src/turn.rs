//! Turned / slanted / taller pasted notes (merge.turned_notes): fastloops' turn_sample, turn_layers. The same
//! sums in the same order as merge._sampled's NumPy code and merge.overlap_layers' heap loop.
use std::cmp::Reverse;
use std::collections::BinaryHeap;

use rayon::prelude::*;

use crate::maths::{f2i, rint};
use crate::pyext::{Out, Outs};
use crate::{grow, need, NO_MEMORY};

/// How many of flat (sorted) are <= x, looked for from hint (the last answer: the next spot's is near it).
#[inline(always)]
fn upper(flat: &[f64], x: f64, hint: usize) -> usize {
    let n = flat.len();
    let h = hint.min(n);
    if h > 0 && flat[h - 1] > x {
        // (the answer is below h: double the step down until a value <= x)
        let mut hi = h - 1; // flat[hi] > x
        let mut step = 1usize;
        let lo;
        loop {
            if hi < step {
                lo = 0;
                break;
            }
            let c = hi - step;
            if flat[c] <= x {
                lo = c + 1;
                break;
            }
            hi = c;
            step <<= 1;
        }
        lo + flat[lo..hi].partition_point(|&v| v <= x)
    } else {
        // (the answer is h or above: double the step up until a value > x)
        let mut lo = h;
        let mut step = 1usize;
        let hi;
        loop {
            let c = lo + step - 1;
            if c >= n {
                hi = n;
                break;
            }
            if flat[c] > x {
                hi = c;
                break;
            }
            lo = c + 1;
            step <<= 1;
        }
        lo + flat[lo..hi].partition_point(|&v| v <= x)
    }
}

/// The numbers _sampled works with (g in this order).
#[derive(Clone, Copy)]
struct Frame {
    b0: f64,
    p0: f64,
    ub: f64,
    up: f64,
    vb: f64,
    vp: f64,
    det: f64,
    t0: f64,
    step: f64,
    t_all: f64,
    k_all: f64,
    ppq: f64,
    e: f64,
}

/// One key row's pieces (start, end, row, which note) added to outs.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn row_pieces(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, kk: f64, row: i64, n: usize,
              outs: &mut [Vec<i64>; 4]) -> Result<(), i64> {
    let last = flat.len() - 1;
    let kmax = g.k_all as i64 - 1;
    let t1 = g.t_all + 1.0;
    let one_e = 1.0 - g.e;
    let dk = kk - g.p0;
    let mut hint = 0usize;
    let mut prev = i64::MIN; // (the value of the stretch going on; MIN = none yet)
    let mut from = 0usize;
    let put = |outs: &mut [Vec<i64>; 4], val: i64, ts: usize, te: usize| -> Result<(), i64> {
        if val >= 0 {
            let start = f2i(rint((g.t0 + ts as f64 * g.step) * g.ppq));
            let end = f2i(rint((g.t0 + te as f64 * g.step) * g.ppq));
            if end > start {
                grow(&mut outs[0], start)?;
                grow(&mut outs[1], end)?;
                grow(&mut outs[2], row)?;
                grow(&mut outs[3], val)?;
            }
        }
        Ok(())
    };
    for j in 0..n {
        let bb = g.t0 + (j as f64 + 0.5) * g.step;
        let db = bb - g.b0;
        let u = (db * g.vp - dk * g.vb) / g.det;
        let v = (dk * g.ub - g.up * db) / g.det;
        let mut a = -1i64;
        if u >= -g.e && u < one_e && v >= -g.e && v < one_e {
            let key = f2i(v * g.k_all).clamp(0, kmax);
            let tick = u * g.t_all;
            let up = upper(flat, key as f64 * t1 + tick, hint);
            hint = up;
            if up >= 1 {
                let at = (up - 1).min(last);
                if skey[at] == key && tick < send[at] as f64 {
                    a = at as i64;
                }
            }
        }
        if j == 0 || a != prev {
            if j > 0 {
                put(outs, prev, from, j)?;
            }
            prev = a;
            from = j;
        }
    }
    if n > 0 {
        put(outs, prev, from, n)?;
    }
    Ok(())
}

/// The rows lo .. hi's pieces, row after row.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn rows_pieces(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, k0: i64, lo: i64, hi: i64, n: usize)
               -> Result<[Vec<i64>; 4], i64> {
    let mut outs: [Vec<i64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for r in lo..hi {
        let k = k0 + r;
        row_pieces(flat, skey, send, g, k as f64, k, n, &mut outs)?;
    }
    Ok(outs)
}

/// fastloops.turn_sample: one layer of notes sorted by key, then start (flat = key x (t_all + 1) + start, their
/// keys, ends), g = (b0, p0, ub, up, vb, vp, det, t0, step, t_all, k_all, ppq, e), key rows lo_k .. hi_k, n time
/// steps -> (start, end, key row, which note) of every piece, row after row (rows shared out when many).
#[inline(always)]
pub fn turn_sample(flat: &[f64], skey: &[i64], send: &[i64], g: &[f64], lo_k: i64, hi_k: i64, n: i64, many: bool)
                   -> Outs {
    let m = flat.len();
    if g.len() < 13 || skey.len() < m || send.len() < m || n < 0 {
        return Err(crate::FAILED);
    }
    let g = Frame {
        b0: g[0], p0: g[1], ub: g[2], up: g[3], vb: g[4], vp: g[5], det: g[6], t0: g[7], step: g[8], t_all: g[9],
        k_all: g[10], ppq: g[11], e: g[12],
    };
    let (skey, send, n) = (&skey[..m], &send[..m], n as usize);
    let rows = (hi_k - lo_k + 1).max(0);
    if m == 0 || rows == 0 || n == 0 {
        return Ok((0..4).map(|_| Out::I64(Vec::new(), 0)).collect());
    }
    let spots = (rows as usize).saturating_mul(n);
    let parts: Vec<[Vec<i64>; 4]> = match crate::pool_for(many, spots, 4) {
        None => vec![need!(rows_pieces(flat, skey, send, &g, lo_k, 0, rows, n))],
        Some(pool) => {
            let per = ((rows as usize) / (pool.current_num_threads() * 4)).max(1) as i64;
            let cuts: Vec<i64> = (0..rows).step_by(per as usize).collect();
            let got: Vec<Result<[Vec<i64>; 4], i64>> = pool.install(|| {
                cuts.par_iter().map(|&a| rows_pieces(flat, skey, send, &g, lo_k, a, (a + per).min(rows), n)).collect()
            });
            let mut parts = Vec::new();
            parts.try_reserve_exact(got.len()).map_err(|_| NO_MEMORY)?;
            for p in got {
                parts.push(p?);
            }
            parts
        }
    };
    let total: usize = parts.iter().map(|p| p[0].len()).sum();
    let mut out: [Vec<i64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for o in out.iter_mut() {
        o.try_reserve_exact(total).map_err(|_| NO_MEMORY)?;
    }
    for p in parts {
        for (o, q) in out.iter_mut().zip(p.iter()) {
            o.extend_from_slice(q);
        }
    }
    let [a, b, c, d] = out;
    Ok(vec![Out::I64(a, 0), Out::I64(b, 0), Out::I64(c, 0), Out::I64(d, 0)])
}

/// fastloops.turn_layers: notes sorted by key, then start (s, e, k), first / over = overlap_layers' marks ->
/// each note's layer into lay (the first free one; notes outside the overlapping stretches stay 0) -> 0.
#[inline(always)]
pub fn turn_layers(s: &[i64], e: &[i64], k: &[i64], first: &[u8], over: &[u8], lay: &mut [i64]) -> i64 {
    let n = s.len();
    if e.len() < n || k.len() < n || first.len() < n || over.len() < n || lay.len() < n {
        return crate::FAILED;
    }
    let mut free: BinaryHeap<Reverse<i64>> = BinaryHeap::new();
    let mut ends: BinaryHeap<Reverse<(i64, i64)>> = BinaryHeap::new();
    let mut key = i64::MIN;
    let mut used = 0i64;
    let mut started = false;
    for i in 0..n {
        if first[i] == 0 {
            continue;
        }
        if over[i] == 0 || !started || k[i] != key {
            // (a new stretch: every layer free again)
            free.clear();
            ends.clear();
            key = k[i];
            started = true;
            if ends.try_reserve(1).is_err() {
                return NO_MEMORY;
            }
            ends.push(Reverse((e[i], 0)));
            used = 1;
            lay[i] = 0;
            continue;
        }
        let si = s[i];
        while let Some(&Reverse((end, l))) = ends.peek() {
            if end > si {
                break;
            }
            ends.pop();
            if free.try_reserve(1).is_err() {
                return NO_MEMORY;
            }
            free.push(Reverse(l));
        }
        let j = match free.pop() {
            Some(Reverse(j)) => j,
            None => {
                used += 1;
                used - 1
            }
        };
        lay[i] = j;
        if ends.try_reserve(1).is_err() {
            return NO_MEMORY;
        }
        ends.push(Reverse((e[i], j)));
    }
    0
}

