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

/// One key row's pieces (start, end, row, which note) added to outs: every step looked at (dense shapes: the
/// jumping loop's own upkeep cost them 7-10 %) or jumping over steps that can't change (fastloops.turn_sample).
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn row_pieces(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, kk: f64, row: i64, n: usize,
              outs: &mut [Vec<i64>; 4], jump: bool) -> Result<(), i64> {
    if jump {
        row_jumps(flat, skey, send, g, kk, row, n, outs)
    } else {
        row_steps(flat, skey, send, g, kk, row, n, outs)
    }
}

/// row_pieces looking at every step.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn row_steps(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, kk: f64, row: i64, n: usize,
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

/// row_pieces jumping: a spot that stayed on one note / gap for JUMP_AFTER steps jumps to just before the next
/// place anything could change (turn_jump; the same pieces, heavy sweep 2026-11-16).
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn row_jumps(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, kk: f64, row: i64, n: usize,
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
    let mut j = 0usize;
    let mut try_at = JUMP_AFTER; // (the step a jump is next worked out at: a run's start + JUMP_AFTER, or past a
    while j < n { // near change)
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
            try_at = j + JUMP_AFTER;
        } else if j >= try_at {
            let to = turn_jump(flat, skey, send, g, dk, a, hint, j, n);
            if to > j as i64 + 1 {
                j = (to as usize).min(n);
                continue;
            }
            try_at = (to + 2 * JUMP_ROOM as i64 + 1).max(j as i64 + 1) as usize; // (the change is near)
        }
        j += 1;
    }
    if n > 0 {
        put(outs, prev, from, n)?;
    }
    Ok(())
}

/// fastloops._cross: steps from a value to a level it moves towards (slope a step), kept in best when nearer; a
/// level just behind (within JUMP_ROOM: a spot right on a key edge) counts as here.
#[inline(always)]
fn cross(level: f64, val: f64, slope: f64, best: &mut f64) {
    if slope != 0.0 {
        let d = (level - val) / slope;
        if d >= -JUMP_ROOM {
            let d = d.max(0.0);
            if d < *best {
                *best = d;
            }
        }
    }
}

/// fastloops.turn_jump: the next step to look at from step j of the row dk (its spot found note a, or -1; upper()
/// = up when inside): every step before it finds the same (JUMP_ROOM steps short of the nearest box edge, key edge
/// or note start / end the spot moves towards), n when nothing more changes. Works the spot out again (the same
/// sums as the step loop).
#[inline(never)] // (rare: kept out of the step loop, which it crowded: 1.3-1.6x slower)
#[allow(clippy::too_many_arguments)]
fn turn_jump(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, dk: f64, a: i64, up: usize, j: usize, n: usize)
             -> i64 {
    let (uu, vv) = (g.step * g.vp / g.det, -g.up * g.step / g.det); // (u, v change by these a step)
    let db = g.t0 + (j as f64 + 0.5) * g.step - g.b0;
    let u = (db * g.vp - dk * g.vb) / g.det;
    let v = (dk * g.ub - g.up * db) / g.det;
    let one_e = 1.0 - g.e;
    let inside = u >= -g.e && u < one_e && v >= -g.e && v < one_e;
    let key = f2i(v * g.k_all).clamp(0, g.k_all as i64 - 1);
    let mut best = f64::INFINITY;
    cross(-g.e, u, uu, &mut best);
    cross(one_e, u, uu, &mut best);
    cross(-g.e, v, vv, &mut best);
    cross(one_e, v, vv, &mut best);
    if inside {
        let kv = (v * g.k_all).floor();
        cross(kv / g.k_all, v, vv, &mut best);
        cross((kv + 1.0) / g.k_all, v, vv, &mut best);
        if g.t_all > 0.0 {
            let kt = key as f64 * (g.t_all + 1.0);
            if a >= 0 {
                note_ends(flat, send, a as usize, kt, g.t_all, u, uu, &mut best);
            } else {
                let last = flat.len() - 1;
                if up >= 1 && skey[(up - 1).min(last)] == key {
                    note_ends(flat, send, (up - 1).min(last), kt, g.t_all, u, uu, &mut best);
                }
                if up <= last && skey[up] == key {
                    note_ends(flat, send, up, kt, g.t_all, u, uu, &mut best);
                }
            }
        }
    }
    if best > n as f64 {
        return n as i64;
    }
    j as i64 + (best.floor() - JUMP_ROOM) as i64 // (j + 1 or less = too near to jump: the caller waits past it)
}

/// fastloops._note_ends: note i's start and end (as u) into best (kt = its key x (t_all + 1)).
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn note_ends(flat: &[f64], send: &[i64], i: usize, kt: f64, t_all: f64, u: f64, uu: f64, best: &mut f64) {
    cross((flat[i] - kt) / t_all, u, uu, best);
    cross(send[i] as f64 / t_all, u, uu, best);
}

/// fastloops.JUMP_AFTER: a spot tries a jump once it has found the same for this many steps.
const JUMP_AFTER: usize = 8;

/// fastloops.JUMP_SPARSE: spots per note above which a call jumps (below: every spot looked at, as before).
const JUMP_SPARSE: usize = 512;

/// fastloops.JUMP_ROOM: steps kept short of a worked-out change (rounding in u, v and the keys' numbers).
const JUMP_ROOM: f64 = 4.0;

// (the pool's work built at the processor's level: a closure handed to the pool is built at the plain level)
level_builds!(#[allow(clippy::too_many_arguments)]
              fn rows_pieces_l(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, k0: i64, lo: i64, hi: i64, n: usize,
                               jump: bool) -> Result<[Vec<i64>; 4], i64> => rows_pieces);

/// The rows lo .. hi's pieces, row after row.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn rows_pieces(flat: &[f64], skey: &[i64], send: &[i64], g: &Frame, k0: i64, lo: i64, hi: i64, n: usize, jump: bool)
               -> Result<[Vec<i64>; 4], i64> {
    let mut outs: [Vec<i64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for r in lo..hi {
        let k = k0 + r;
        row_pieces(flat, skey, send, g, k as f64, k, n, &mut outs, jump)?;
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
    let jump = spots > JUMP_SPARSE.saturating_mul(m); // (many more spots than notes: jumping pays)
    let parts: Vec<[Vec<i64>; 4]> = match crate::pool_for(many, spots, 4) {
        None => vec![need!(rows_pieces(flat, skey, send, &g, lo_k, 0, rows, n, jump))],
        Some(pool) => {
            let per = ((rows as usize) / (pool.current_num_threads() * 4)).max(1) as i64;
            let cuts: Vec<i64> = (0..rows).step_by(per as usize).collect();
            let got: Vec<Result<[Vec<i64>; 4], i64>> = pool.install(|| {
                cuts.par_iter().map(|&a| rows_pieces_l(flat, skey, send, &g, lo_k, a, (a + per).min(rows), n, jump)).collect()
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

