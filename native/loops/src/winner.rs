//! Remove overlaps, split around (engine.winner_stretches): fastloops.winner_pieces' twin.
//! The places in order are checked once up front; inside, nothing is checked again (every index comes from them).
#![allow(unused_unsafe)] // (at!(a, at!(b, i)): the inner one is inside the outer unsafe already)
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

/// A note (rank r, place i) into the heap (lowest rank, then place, on top) -> its new size.
#[inline(always)]
fn heap_push(hr: &mut [i64], hi: &mut [i64], size: usize, r: i64, i: i64) -> usize {
    let mut k = size;
    put!(hr, k, r);
    put!(hi, k, i);
    while k > 0 {
        let p = (k - 1) / 2;
        let (rp, rk, ip, ik) = (at!(hr, p), at!(hr, k), at!(hi, p), at!(hi, k));
        if rp < rk || (rp == rk && ip <= ik) {
            break;
        }
        put!(hr, p, rk);
        put!(hr, k, rp);
        put!(hi, p, ik);
        put!(hi, k, ip);
        k = p;
    }
    size + 1
}

/// The top of the heap taken off -> its new size.
#[inline(always)]
fn heap_pop(hr: &mut [i64], hi: &mut [i64], size: usize) -> usize {
    let size = size - 1;
    put!(hr, 0, at!(hr, size));
    put!(hi, 0, at!(hi, size));
    let mut k = 0;
    loop {
        let (a, b) = (2 * k + 1, 2 * k + 2);
        let mut m = k;
        if a < size && (at!(hr, a) < at!(hr, m) || (at!(hr, a) == at!(hr, m) && at!(hi, a) < at!(hi, m))) {
            m = a;
        }
        if b < size && (at!(hr, b) < at!(hr, m) || (at!(hr, b) == at!(hr, m) && at!(hi, b) < at!(hi, m))) {
            m = b;
        }
        if m == k {
            return size;
        }
        let (rm, rk, im, ik) = (at!(hr, m), at!(hr, k), at!(hi, m), at!(hi, k));
        put!(hr, m, rk);
        put!(hr, k, rm);
        put!(hi, m, ik);
        put!(hi, k, im);
        k = m;
    }
}

/// In each group, at every moment the sounding note with the lowest rank wins; walked once in time (order: by
/// group, then start) with the sounding notes in a heap. -> how many pieces (note / ps / pe filled up to it).
/// Needs: every order[i] < the rows of group / s / e / rank; hr / hi as long as order.
#[inline(always)]
fn pieces_inner(group: &[i64], s: &[i64], e: &[i64], rank: &[i64], order: &[i64], note: &mut [i64], ps: &mut [i64],
                pe: &mut [i64], hr: &mut [i64], hi: &mut [i64]) -> i64 {
    let n = order.len();
    let most = note.len().min(ps.len()).min(pe.len());
    let (mut m, mut i) = (0usize, 0usize);
    while i < n {
        let g = at!(group, at!(order, i));
        let (mut size, mut cur, mut since) = (0usize, -1i64, 0i64);
        let mut t = at!(s, at!(order, i));
        loop {
            while i < n && at!(group, at!(order, i)) == g && at!(s, at!(order, i)) <= t {
                // (every note starting by now)
                let o = at!(order, i);
                size = heap_push(hr, hi, size, at!(rank, o), o);
                i += 1;
            }
            while size > 0 && at!(e, at!(hi, 0)) <= t {
                // (ended ones off the top)
                size = heap_pop(hr, hi, size);
            }
            let top = if size > 0 { at!(hi, 0) } else { -1 };
            if top != cur {
                if cur >= 0 && t > since {
                    if m == most {
                        // (never: a note's pieces end at a start or an end, 2 per note at most; as Numba's break)
                        break;
                    }
                    put!(note, m, cur);
                    put!(ps, m, since);
                    put!(pe, m, t);
                    m += 1;
                }
                cur = top;
                since = t;
            }
            let nxt = if i < n && at!(group, at!(order, i)) == g { at!(s, at!(order, i)) } else { -1 };
            if cur < 0 && nxt < 0 {
                break;
            }
            if cur < 0 {
                t = nxt;
            } else if nxt >= 0 && nxt < at!(e, cur) {
                t = nxt;
            } else {
                t = at!(e, cur);
            }
        }
    }
    m as i64
}

two_builds!(fn pieces_built(group: &[i64], s: &[i64], e: &[i64], rank: &[i64], order: &[i64], note: &mut [i64],
                            ps: &mut [i64], pe: &mut [i64], hr: &mut [i64], hi: &mut [i64]) -> i64 => pieces_inner);

pub fn pieces(group: &[i64], s: &[i64], e: &[i64], rank: &[i64], order: &[i64], note: &mut [i64], ps: &mut [i64],
              pe: &mut [i64]) -> i64 {
    let rows = group.len().min(s.len()).min(e.len()).min(rank.len());
    if order.iter().any(|&o| o < 0 || o as usize >= rows) {
        panic!("a place past the rows"); // (caught at the C edge: the caller uses its slower code)
    }
    let n = order.len();
    let (Some(mut hr), Some(mut hi)) = (try_zeros::<i64>(n), try_zeros::<i64>(n)) else {
        return NO_MEMORY;
    };
    pieces_built(group, s, e, rank, order, note, ps, pe, &mut hr, &mut hi)
}
