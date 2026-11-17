//! Pasted notes in a moved / flipped / stretched-sideways box (notes/custom.py block_notes' own way): each note a
//! flat line in the box, cut into one note per key it crosses (notes/paths.py parts_notes with two-point parts).
//! The same numbers as that NumPy code, step for step (Rust never fuses a * b + c); notes shared out on half the
//! cores. Two calls: block_count (each note's piece count, so Python makes the output) then block_fill.
//! Odd input (a point that isn't a finite number, a piece count past MOST) = FAILED: NumPy's way does that call.

use crate::maths::{f2i, rint};
use crate::{pool_for, Nd, NdMut, FAILED};
use rayon::prelude::*;

/// More keys crossed by one note than this = odd (FAILED: NumPy's way).
const MOST: i64 = 1 << 24;

/// The box: b0, p0, ub, up, vb, vp, t_all, k_all, ppq, row_edge (paths.ROW_EDGE).
struct Box {
    b0: f64,
    p0: f64,
    ub: f64,
    up: f64,
    vb: f64,
    vp: f64,
    t_all: f64,
    k_all: f64,
    ppq: f64,
    re: f64,
}

/// A note's two ends, left to right: (ta, ya, tb, yb), their rows (pa, pb). None = odd.
#[inline(always)]
fn ends(b: &Box, s: i64, e: i64, k: i64) -> Option<(f64, f64, f64, f64, i64, i64)> {
    let v = (k as f64 + 0.5) / b.k_all;
    let u0 = s as f64 / b.t_all;
    let u1 = e as f64 / b.t_all;
    let (mut ta, mut ya) = ((b.b0 + u0 * b.ub + v * b.vb) * b.ppq, b.p0 + u0 * b.up + v * b.vp);
    let (mut tb, mut yb) = ((b.b0 + u1 * b.ub + v * b.vb) * b.ppq, b.p0 + u1 * b.up + v * b.vp);
    if !(ta.is_finite() && ya.is_finite() && tb.is_finite() && yb.is_finite()) {
        return None;
    }
    if ta > tb || (ta == tb && ya > yb) {
        std::mem::swap(&mut ta, &mut tb);
        std::mem::swap(&mut ya, &mut yb);
    }
    let mut pa = f2i((ya + 0.5).floor());
    let mut pb = f2i((yb + 0.5).floor());
    // (a point on the edge between two rows belongs to the row below when the line goes down from it: parts_notes)
    let ha = ya + 0.5;
    if (ha - rint(ha)).abs() < b.re {
        pa = f2i(rint(ha));
        if yb < ya - b.re {
            pa -= 1;
        }
    }
    let hb = yb + 0.5;
    if (hb - rint(hb)).abs() < b.re {
        pb = f2i(rint(hb));
        if ya < yb - b.re {
            pb -= 1;
        }
    }
    if (pb - pa).abs() >= MOST || pa.abs() >= MOST || pb.abs() >= MOST {
        return None;
    }
    Some((ta, ya, tb, yb, pa, pb))
}

#[inline(always)]
fn the_box(g: &[f64]) -> Option<Box> {
    if g.len() < 10 {
        return None;
    }
    Some(Box { b0: g[0], p0: g[1], ub: g[2], up: g[3], vb: g[4], vp: g[5], t_all: g[6], k_all: g[7], ppq: g[8], re: g[9] })
}

// (each chunk's work built at the processor's level: a closure handed to the pool is built at the plain level,
// where floor / round are library calls, 20x slower)
level_builds!(fn count_range(b: &Box, rows: &[i64], nc: usize, a: usize, cnt: &mut [i64]) -> bool => count_range_inner);
level_builds!(fn fill_range(b: &Box, rows: &[i64], nc: usize, a: usize, offs: &[i64], off0: i64, out: &mut [i64])
              => fill_range_inner);

/// Notes a .. b's piece counts into cnt (cnt[0] = note a's). false = odd.
#[inline(always)]
fn count_range_inner(b: &Box, rows: &[i64], nc: usize, a: usize, cnt: &mut [i64]) -> bool {
    for (j, c) in cnt.iter_mut().enumerate() {
        let i = (a + j) * nc;
        match ends(b, rows[i], rows[i + 1], rows[i + 2]) {
            Some((_, _, _, _, pa, pb)) => *c = (pb - pa).abs() + 1,
            None => return false,
        }
    }
    true
}

/// custom.block_notes' parts: rows = (start, end, key, velocity, track) notes, g = the box -> each note's piece
/// count into cnt -> all of them (FAILED: odd input, NumPy's way does it).
#[inline(always)]
pub fn block_count(rows: Nd<i64, 2>, g: &[f64], cnt: &mut [i64], many: bool) -> i64 {
    let (n, nc) = (rows.sh[0], rows.sh[1]);
    let b = match the_box(g) {
        Some(b) => b,
        None => return FAILED,
    };
    if nc < 5 || cnt.len() < n || rows.d.len() < n * nc {
        return FAILED;
    }
    let (rd, cnt) = (rows.d, &mut cnt[..n]);
    let ok = match pool_for(many, n, 1) {
        None => count_range(&b, rd, nc, 0, cnt),
        Some(pool) => {
            let per = (n / (pool.current_num_threads() * 4)).max(1024);
            pool.install(|| cnt.par_chunks_mut(per).enumerate().all(|(c, part)| count_range(&b, rd, nc, c * per, part)))
        }
    };
    if !ok {
        return FAILED;
    }
    cnt.iter().sum()
}

/// One note's pieces into out (m rows of 5: start, end, key, velocity, track): parts_notes for its two ends.
#[inline(always)]
fn note_pieces(b: &Box, s: i64, e: i64, k: i64, vel: i64, trk: i64, out: &mut [i64]) {
    let (ta, ya, tb, yb, pa, pb) = match ends(b, s, e, k) {
        Some(x) => x,
        None => return, // (counted already: can't happen)
    };
    let m = out.len() / 5;
    let st = (pb - pa).signum();
    let stf = 0.5 * st as f64;
    // entries: the first point, then each row edge crossed
    out[0] = f2i((ta + 0.5).floor());
    out[2] = pa;
    for j in 1..m {
        let q = pa + st * j as i64;
        let yy = q as f64 - stf;
        let tt = ta + (tb - ta) * (yy - ya) / (yb - ya);
        out[j * 5] = f2i((tt + 0.5).floor());
        out[j * 5 + 2] = q;
    }
    // a note lasts until the next one starting on a later tick (steep: all but the last on a tick get 1 tick)
    let end_tick = f2i((tb + 0.5).floor());
    let first_tick = out[0];
    let mut nxt = end_tick;
    for j in (0..m).rev() {
        let sj = out[j * 5];
        let mut e = end_tick;
        if j + 1 < m {
            let sn = out[(j + 1) * 5];
            if sn > sj {
                nxt = sn;
            }
            e = nxt;
            if sn == sj {
                e = sj + 1; // (tied)
            }
        }
        let mut en = if e > sj + 1 { e } else { sj + 1 };
        if j + 1 < m && out[(j + 1) * 5] == sj {
            en = sj + 1;
        }
        out[j * 5 + 1] = en;
    }
    // keys reached on a sloped note's very last tick: that tick's note ends where the note does
    if first_tick < end_tick {
        for j in 0..m {
            if out[j * 5] == end_tick {
                out[j * 5] -= 1;
                out[j * 5 + 1] = out[j * 5] + 1;
            }
        }
    }
    for j in 0..m {
        out[j * 5 + 3] = vel;
        out[j * 5 + 4] = trk;
    }
}

/// Notes a .. a + offs.len() into out (their rows from off0 on).
#[inline(always)]
fn fill_range_inner(b: &Box, rows: &[i64], nc: usize, a: usize, offs: &[i64], off0: i64, out: &mut [i64]) {
    let total = out.len() as i64 / 5;
    for (j, &o) in offs.iter().enumerate() {
        let i = (a + j) * nc;
        let next = if j + 1 < offs.len() { offs[j + 1] } else { off0 + total };
        let (lo, hi) = (((o - off0) * 5) as usize, ((next - off0) * 5) as usize);
        note_pieces(b, rows[i], rows[i + 1], rows[i + 2], rows[i + 3], rows[i + 4], &mut out[lo..hi]);
    }
}

/// custom.block_notes' parts, second call: offs = where each note's pieces start (cumsum of block_count's), out =
/// (all pieces, 5) -> 0.
#[inline(always)]
pub fn block_fill(rows: Nd<i64, 2>, g: &[f64], offs: &[i64], out: NdMut<i64, 2>, many: bool) -> i64 {
    let (n, nc) = (rows.sh[0], rows.sh[1]);
    let b = match the_box(g) {
        Some(b) => b,
        None => return FAILED,
    };
    let total = out.sh[0];
    if nc < 5 || out.sh[1] != 5 || offs.len() < n || rows.d.len() < n * nc || out.d.len() < total * 5 {
        return FAILED;
    }
    let offs = &offs[..n];
    // (offsets must run up from 0 within out, else odd)
    let mut last = 0i64;
    for &o in offs {
        if o < last || o > total as i64 {
            return FAILED;
        }
        last = o;
    }
    if n > 0 && offs[0] != 0 {
        return FAILED;
    }
    let (rd, od) = (rows.d, &mut out.d[..total * 5]);
    match pool_for(many, n, 1) {
        None => fill_range(&b, rd, nc, 0, offs, 0, od),
        Some(pool) => {
            let per = (n / (pool.current_num_threads() * 4)).max(1024);
            // (the output cut where each chunk's first note starts)
            let mut jobs = Vec::new();
            let mut rest: &mut [i64] = od;
            let mut a = 0usize;
            while a < n {
                let z = (a + per).min(n);
                let hi = if z < n { offs[z] } else { total as i64 };
                let (mine, more) = std::mem::take(&mut rest).split_at_mut(((hi - offs[a]) * 5) as usize);
                jobs.push((a, z, mine));
                rest = more;
                a = z;
            }
            pool.install(|| {
                jobs.into_par_iter().for_each(|(a, z, part)| fill_range(&b, rd, nc, a, &offs[a..z], offs[a], part))
            });
        }
    }
    0
}
