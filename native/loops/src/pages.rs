//! Note tool pages on big pastes (2026-11-21, heavy sweep item 5): Chop's pieces (notes/chop.py _pieces), each key
//! row's gaps (notes/sustain.py row_order) and Gate aware flip's runs mirrored (notes/gateflip.py _mirrored). The very
//! same notes as the NumPy way, which is their twin (not in NEEDS: no plain twin). Odd input = FAILED: the NumPy way
//! does that call.

use crate::maths::rint;
use crate::{all_below, rd, wr, Nd, NdMut, FAILED};

/// np.maximum for numbers that are never NaN (checked by the caller): the bigger.
#[inline(always)]
fn fmax(a: f64, b: f64) -> f64 {
    if a >= b {
        a
    } else {
        b
    }
}

#[inline(always)]
fn fmin(a: f64, b: f64) -> f64 {
    if a <= b {
        a
    } else {
        b
    }
}

#[inline(always)]
fn chop_inner(a: &[i64], nc: usize, origin: &[f64], k0: &[i64], reps: &[i64], ps: &[f64], pe: &[f64], vk: &[f64],
              vmode: i64, keep: f64, cycle: f64, out: &mut [i64]) -> usize {
    let np_ = ps.len();
    let mut m = 0usize;
    for i in 0..origin.len() {
        let row = i * nc;
        let s = rd(a, row) as f64;
        let e = rd(a, row + 1) as f64;
        let o = rd(origin, i);
        let k0i = rd(k0, i);
        for r in 0..rd(reps, i) {
            let base = o + ((k0i + r) as f64) * cycle;
            for w in 0..np_ {
                let lo = rint(fmax(base + rd(ps, w), s));
                let hi = rint(fmin(base + rd(pe, w), e));
                if !(hi > lo) {
                    continue;
                }
                let at = m * nc;
                for c in 0..nc {
                    wr(out, at + c, rd(a, row + c));
                }
                wr(out, at, lo as i64);
                wr(out, at + 1, hi as i64);
                if vmode != 0 {
                    let v = rd(a, row + 3) as f64;
                    let x = if vmode == 2 { rint(keep * v + rd(vk, w)) } else { rint(v * rd(vk, w)) };
                    wr(out, at + 3, x.max(1.0).min(127.0) as i64);
                }
                m += 1;
            }
        }
    }
    m
}
level_builds!(fn chop_l(a: &[i64], nc: usize, origin: &[f64], k0: &[i64], reps: &[i64], ps: &[f64],
                               pe: &[f64], vk: &[f64], vmode: i64, keep: f64, cycle: f64, out: &mut [i64]) -> usize
                     => chop_inner);

/// chop._pieces for every note at once. a: the notes (start, end, key, velocity, other columns ride along), each
/// note's repeats k0[i] .. k0[i] + reps[i] - 1 counted from origin[i], one repeat cycle ticks; the rhythm's pieces
/// ps .. pe (ticks in a repeat). vmode 0 = velocities kept; 1 = % of the note's (vk = each piece's share, worked out
/// by NumPy as _pieces does); 2 = fixed (rint(keep * v + vk), vk = amt * the piece's velocity, keep = 1 - amt). The
/// pieces left with a length written into out note by note -> how many (out has room for every repeat's pieces).
#[inline(always)]
pub fn chop_cut(a: Nd<i64, 2>, origin: &[f64], k0: &[i64], reps: &[i64], ps: &[f64], pe: &[f64], vk: &[f64],
                vmode: i64, keep: f64, cycle: f64, out: NdMut<i64, 2>) -> i64 {
    let (n, nc) = (a.sh[0], a.sh[1]);
    let np_ = ps.len();
    if nc < 2 || (vmode != 0 && nc < 4) || out.sh[1] != nc || origin.len() != n || k0.len() < n || reps.len() < n
        || pe.len() < np_ || (vmode != 0 && vk.len() < np_) || a.d.len() < n * nc || !(cycle.is_finite())
        || !(-1e300..1e300).contains(&keep) {
        return FAILED;
    }
    let mut total = 0i64;
    for i in 0..n {
        let r = reps[i];
        if r < 0 || !origin[i].is_finite() {
            return FAILED;
        }
        total = match total.checked_add(r) {
            Some(t) => t,
            None => return FAILED,
        };
    }
    if ps.iter().chain(pe.iter()).any(|x| !x.is_finite()) || vk[..if vmode != 0 { np_ } else { 0 }].iter().any(|x| !x.is_finite()) {
        return FAILED;
    }
    let room = (total as u128) * (np_ as u128);
    if room > out.sh[0] as u128 || out.d.len() < out.sh[0] * nc {
        return FAILED;
    }
    chop_l(a.d, nc, origin, k0, reps, ps, pe, vk, vmode, keep, cycle, out.d) as i64
}

#[inline(always)]
fn gaps_inner(nt: &[i64], nc: usize, order: &[i64], gap: &mut [i64], dup: &mut [u8]) {
    let n = order.len();
    let (mut ps, mut pe, mut pp, mut ran) = (0i64, 0i64, 0i64, 0i64);
    for idx in 0..n {
        let i = rd(order, idx) as usize * nc;
        let (s, e, p) = (rd(nt, i), rd(nt, i + 1), rd(nt, i + 2));
        if idx == 0 || p != pp {
            wr(gap, idx, -1);
            wr(dup, idx, 0);
            ran = e;
        } else {
            wr(gap, idx, (s - ran).max(0));
            wr(dup, idx, (s == ps && e == pe) as u8);
            ran = ran.max(e);
        }
        ps = s;
        pe = e;
        pp = p;
    }
}
level_builds!(fn gaps_l(nt: &[i64], nc: usize, order: &[i64], gap: &mut [i64], dup: &mut [u8]) => gaps_inner);

/// sustain.row_order's gaps: notes (start, end, key, ...) in order (key by key, in time) -> each one's gap to what
/// sounded before it on its key (-1: a key's first note) into gap, and into dup 1 where it's an exact copy (same
/// start, end, key) of the note before it in order (gateflip._mirrored's dup) -> 0.
#[inline(always)]
pub fn row_gaps(notes: Nd<i64, 2>, order: &[i64], gap: &mut [i64], dup: &mut [u8]) -> i64 {
    let (rows, nc) = (notes.sh[0], notes.sh[1]);
    let n = order.len();
    if nc < 3 || notes.d.len() < rows * nc || gap.len() < n || dup.len() < n || !all_below(order, rows) {
        return FAILED;
    }
    gaps_l(notes.d, nc, order, gap, dup);
    0
}

#[inline(always)]
fn mirror_inner(nt: &[i64], nc: usize, order: &[i64], gap: &[i64], dup: &[u8], usual: i64, carry: bool,
                out: &mut [i64]) {
    let n = order.len();
    let mut f = 0usize;
    while f < n {
        // (one run: f .. l, the next starting where a gap is past the usual one, or a key's first note; an exact copy
        // never starts one)
        let i0 = rd(order, f) as usize * nc;
        let (mut lo, mut hi) = (rd(nt, i0), rd(nt, i0 + 1));
        let mut l = f + 1;
        while l < n {
            let g = rd(gap, l);
            if g < 0 || (g > usual && rd(dup, l) == 0) {
                break;
            }
            let i = rd(order, l) as usize * nc;
            lo = lo.min(rd(nt, i));
            hi = hi.max(rd(nt, i + 1));
            l += 1;
        }
        let both = lo + hi;
        for idx in f..l {
            let i = rd(order, idx) as usize * nc;
            for c in 0..nc {
                wr(out, i + c, rd(nt, i + c));
            }
            wr(out, i, both - rd(nt, i + 1));
            wr(out, i + 1, both - rd(nt, i));
            if !carry && nc > 3 {
                let j = rd(order, f + l - 1 - idx) as usize * nc;
                wr(out, i + 3, rd(nt, j + 3));
            }
        }
        f = l;
    }
}
level_builds!(fn mirror_l(nt: &[i64], nc: usize, order: &[i64], gap: &[i64], dup: &[u8], usual: i64,
                                 carry: bool, out: &mut [i64]) => mirror_inner);

/// gateflip.mirrored_runs' notes: row_gaps' gap / dup in order, the usual gap -> each run on a key mirrored inside
/// itself (its first start and last end kept), written into out at each note's own row; velocities taken from the
/// note at the other end of its run unless carry -> 0. Every note must be in order once.
#[inline(always)]
pub fn run_mirror(notes: Nd<i64, 2>, order: &[i64], gap: &[i64], dup: &[u8], usual: i64, carry: bool,
                  out: NdMut<i64, 2>) -> i64 {
    let (rows, nc) = (notes.sh[0], notes.sh[1]);
    let n = order.len();
    if nc < 3 || n != rows || notes.d.len() < rows * nc || out.sh[0] != rows || out.sh[1] != nc
        || out.d.len() < rows * nc || gap.len() < n || dup.len() < n || !all_below(order, rows) {
        return FAILED;
    }
    mirror_l(notes.d, nc, order, gap, dup, usual, carry, out.d);
    0
}
