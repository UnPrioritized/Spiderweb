//! The piano roll's note pictures (roll_draw): fastloops' paint_order, order_screen, order_rects, last_on_pixels,
//! note_top, paint_notes. The very same steps as those, in the same order, so the same pixels come out.
use crate::maths::{f2i, fdiv, fmodi, imax, imin, rint};
use rayon::prelude::*;
use std::sync::atomic::{AtomicI64, Ordering};
use crate::{all_below, copied, need, rd, room, try_zeros, wr, zeros, Flag, Nd, NdMut, NO_MEMORY};

const OWN0: i64 = 1 << 32; // (engine.OWN0)
const NO_END: i64 = 1 << 30; // (fastloops.NO_END)

/// A place counted from the end when below 0 (Python / Numba's a[-1]).
#[inline(always)]
fn wrap(i: i64, n: usize) -> usize {
    if i < 0 {
        (i + n as i64) as usize
    } else {
        i as usize
    }
}

/// fastloops.paint_order -> the longest note (s, e, key, color, first filled).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn paint_order(notes: Nd<i64, 2>, by_start: &[i64], rank: &[i64], nranks: i64, pics: &[Flag], cols: &[i64],
                   picgroup: i64, ghost: i64, s: &mut [i64], e: &mut [i64], key: &mut [i16], color: &mut [i32],
                   first: &mut [i64]) -> i64 {
    let (rows, nc, n) = (notes.sh[0], notes.sh[1], by_start.len());
    let np = pics.len();
    // (checked once: every rank 0 .. nranks, every place a note with a rank, room for every note, the columns read;
    // then no checks a step. Else the checked loop below)
    let nr = nranks.max(0) as usize;
    if nranks < 0 || nc < (if np > 0 { 6 } else { 5 }) || notes.d.len() < rows * nc || rank.len() < n
        || cols.is_empty() || first.len() < nr + 1 || s.len() < n || e.len() < n || key.len() < n || color.len() < n
        || !all_below(by_start, rows.min(rank.len())) || !all_below(rank, nr) {
        return paint_order_checked(notes, by_start, rank, nranks, pics, cols, picgroup, ghost, s, e, key, color,
                                   first);
    }
    let nt = notes.d;
    let nslots = cols.len() as i64;
    for v in first[..nr + 1].iter_mut() {
        *v = 0;
    }
    for i in 0..n {
        let r = rd(rank, i) as usize;
        wr(first, r + 1, rd(first, r + 1) + 1);
    }
    for r in 0..nr {
        first[r + 1] += first[r];
    }
    let mut at: Vec<i64> = need!(copied(&first[..nr]));
    let mut longest = 0i64;
    for j in 0..n {
        let i = rd(by_start, j) as usize;
        let r = rd(rank, i) as usize;
        let o = rd(&at, r) as usize;
        if o >= n {
            // (by_start not one place per counted note: the checked loop does it all again, as Numba would)
            return paint_order_checked(notes, by_start, rank, nranks, pics, cols, picgroup, ghost, s, e, key, color,
                                       first);
        }
        wr(&mut at, r, o as i64 + 1);
        let p = i * nc;
        let (n0, n1, n2, n3, n4) = (rd(nt, p), rd(nt, p + 1), rd(nt, p + 2), rd(nt, p + 3), rd(nt, p + 4));
        wr(s, o, n0);
        wr(e, o, n1);
        wr(key, o, n2 as i16);
        longest = imax(longest, n1 - n0);
        let sl = n4;
        let mut c = if sl >= OWN0 {
            let ch = sl & 15;
            if ch < 9 {
                ch
            } else if ch == 9 {
                15
            } else {
                ch - 1
            }
        } else {
            rd(cols, fmodi(sl, nslots) as usize)
        };
        if np > 0 && rd(pics, imin(imax(rd(nt, p + 5), 0), np as i64 - 1) as usize).on() {
            c = picgroup + imin(n4, 15);
        }
        if n3 < 0 {
            let v = n3;
            wr(color, o, ((ghost + c) * 32 + if v == -1 { 31 } else { imin(30, fdiv(-v - 2, 4)) }) as i32);
        } else {
            wr(color, o, (c * 32 + fdiv(imin(n3, 127), 4)) as i32);
        }
    }
    longest
}

#[allow(clippy::too_many_arguments)]
#[inline(never)] // (rare odd inputs: kept out of the fast loop's code)
fn paint_order_checked(notes: Nd<i64, 2>, by_start: &[i64], rank: &[i64], nranks: i64, pics: &[Flag], cols: &[i64],
                       picgroup: i64, ghost: i64, s: &mut [i64], e: &mut [i64], key: &mut [i16], color: &mut [i32],
                       first: &mut [i64]) -> i64 {
    let nc = notes.sh[1];
    let nt = notes.d;
    let row = |i: usize, k: usize| nt[i * nc + k];
    let nslots = cols.len() as i64;
    let n = by_start.len();
    let nr = nranks.max(0) as usize;
    for v in first[..nr + 1].iter_mut() {
        *v = 0;
    }
    for i in 0..n {
        first[wrap(rank[i] + 1, nr + 1)] += 1;
    }
    for r in 0..nr {
        first[r + 1] += first[r];
    }
    let mut at: Vec<i64> = need!(copied(&first[..nr]));
    let mut longest = 0i64;
    let np = pics.len() as i64;
    for j in 0..n {
        let i = wrap(by_start[j], notes.sh[0]);
        let r = wrap(rank[i], nr);
        let o = at[r] as usize;
        at[r] += 1;
        let (n0, n1, n2, n3, n4) = (row(i, 0), row(i, 1), row(i, 2), row(i, 3), row(i, 4));
        s[o] = n0;
        e[o] = n1;
        key[o] = n2 as i16;
        longest = imax(longest, n1 - n0);
        let sl = n4;
        let mut c = if sl >= OWN0 {
            let ch = sl & 15;
            if ch < 9 {
                ch
            } else if ch == 9 {
                15
            } else {
                ch - 1
            }
        } else {
            cols[wrap(fmodi(sl, nslots), cols.len())]
        };
        if np > 0 && pics[imin(imax(row(i, 5), 0), np - 1) as usize].on() {
            c = picgroup + imin(n4, 15);
        }
        if n3 < 0 {
            let v = n3;
            color[o] = ((ghost + c) * 32 + if v == -1 { 31 } else { imin(30, fdiv(-v - 2, 4)) }) as i32;
        } else {
            color[o] = (c * 32 + fdiv(imin(n3, 127), 4)) as i32;
        }
    }
    longest
}

/// fastloops._block_range: in s[lo:hi] (sorted) the first with s >= t_lo, the first with s > t_hi.
#[inline(always)]
fn block_range(s: &[i64], lo: i64, hi: i64, t_lo: f64, t_hi: f64) -> (i64, i64) {
    let (mut a, mut b) = (lo, hi);
    while a < b {
        let m = fdiv(a + b, 2);
        if (s[wrap(m, s.len())] as f64) < t_lo {
            a = m + 1;
        } else {
            b = m;
        }
    }
    let (mut c, mut d) = (a, hi);
    while c < d {
        let m = fdiv(c + d, 2);
        if (s[wrap(m, s.len())] as f64) <= t_hi {
            c = m + 1;
        } else {
            d = m;
        }
    }
    (a, c)
}

/// How many notes order_screen / order_rects look at (their outputs' room).
#[inline(always)]
pub fn screen_count(s: &[i64], first: &[i64], b0: i64, b1: i64, t_lo: f64, t_hi: f64) -> i64 {
    let mut m = 0;
    for r in b0..b1 {
        let (lo, hi) = block_range(s, first[wrap(r, first.len())], first[wrap(r + 1, first.len())], t_lo, t_hi);
        m += hi - lo;
    }
    m
}

/// One note's place on screen, or None when it's left out (order_screen's tests, in its order).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn on_screen(s: i64, e: i64, k: i16, ax: f64, bx: f64, kb: f64, w: f64, shown: &[Flag], clip: bool, c0: f64,
             c2: f64) -> Option<(f64, f64)> {
    let a = rint(s as f64 * ax + bx);
    let b = rint(e as f64 * ax + bx);
    if b < kb || a > w || !shown[wrap(k as i64, shown.len())].on() {
        return None;
    }
    if clip && (b < c0 || a >= c2) {
        return None;
    }
    Some((a, b))
}

/// fastloops.order_screen into out (4 rows: x0, x1, key, colour; room for screen_count) -> how many.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn order_screen(s: &[i64], e: &[i64], key: &[i16], color: &[i32], first: &[i64], b0: i64, b1: i64, t_lo: f64,
                    t_hi: f64, ax: f64, bx: f64, kb: f64, w: f64, shown: &[Flag], clip: bool, c0: f64, c2: f64,
                    out: NdMut<i64, 2>) -> i64 {
    let room = out.sh[1];
    let (x0, rest) = out.d.split_at_mut(room);
    let (x1, rest) = rest.split_at_mut(room);
    let (kk, cc) = rest.split_at_mut(room);
    let mut m = 0usize;
    for r in b0..b1 {
        let (lo, hi) = block_range(s, first[wrap(r, first.len())], first[wrap(r + 1, first.len())], t_lo, t_hi);
        for i in lo..hi {
            let i = wrap(i, s.len());
            if let Some((a, b)) = on_screen(s[i], e[i], key[i], ax, bx, kb, w, shown, clip, c0, c2) {
                x0[m] = f2i(a);
                x1[m] = f2i(b);
                kk[m] = key[i] as i64;
                cc[m] = color[i] as i64;
                m += 1;
            }
        }
    }
    m as i64
}

/// fastloops.order_rects into out (5 rows: x0, y0, x1, y1, colour; room for screen_count) -> how many.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn order_rects(s: &[i64], e: &[i64], key: &[i16], color: &[i32], first: &[i64], b0: i64, b1: i64, t_lo: f64,
                   t_hi: f64, ax: f64, bx: f64, kb: f64, w: f64, shown: &[Flag], clip: bool, c0: f64, c2: f64,
                   left: i64, right: i64, keys: i64, row0: &[i64], row1: &[i64], out: NdMut<i64, 2>) -> i64 {
    // (the blocks found with checks (few); then every note in them read with no checks a step: the blocks lie
    // inside the lists, out has room for them all, and each note's key is checked once in the first pass (before
    // its first use), so the second pass, seeing the very same notes, needs none. Odd inputs: the checked loop)
    let lim = s.len().min(e.len()).min(key.len()).min(color.len());
    let kmax = shown.len().min(row0.len()).min(row1.len()).min(keys.max(0) as usize) as u64;
    let nb = (b1 - b0).max(0) as usize;
    let mut ranges = need!(room(nb));
    let mut total = 0usize;
    let mut ok = out.sh[0] >= 5 && out.d.len() >= 5 * out.sh[1];
    for r in b0..b1 {
        let (lo, hi) = block_range(s, first[wrap(r, first.len())], first[wrap(r + 1, first.len())], t_lo, t_hi);
        if lo < hi {
            ok &= lo >= 0 && hi as usize <= lim;
            total += (hi - lo) as usize;
        }
        ranges.push((lo, hi));
    }
    if !ok || total > out.sh[1] {
        return order_rects_checked(s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2,
                                   left, right, keys, row0, row1, out);
    }
    let size_all = (right - left + 1) * keys * 4;
    // (left as it comes, like Numba's np.empty: the second pass only reads the cells the first pass wrote (the very
    // same notes), so zeroing it first is wasted; only touched through the pointer)
    let mut room_last: Vec<i64> = Vec::new();
    if room_last.try_reserve_exact(size_all.max(0) as usize).is_err() {
        return NO_MEMORY;
    }
    let last = room_last.as_mut_ptr();
    // (one note on screen? its x0 / x1 as order_screen works them out; the key already checked)
    let place = |i: usize| -> Option<(i64, i64)> {
        let a = rint(rd(s, i) as f64 * ax + bx);
        let b = rint(rd(e, i) as f64 * ax + bx);
        if b < kb || a > w || !rd(shown, rd(key, i) as usize).on() {
            return None;
        }
        if clip && (b < c0 || a >= c2) {
            return None;
        }
        Some((imax(f2i(a), left), imin(f2i(b), right)))
    };
    let mut j = 0i64;
    for &(lo, hi) in &ranges {
        for i in lo.max(0) as usize..hi.max(0) as usize {
            let k = rd(key, i) as i64;
            if k as u64 >= kmax {
                return order_rects_checked(s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip,
                                           c0, c2, left, right, keys, row0, row1, out);
            }
            if let Some((x0, x1)) = place(i) {
                let size = x1 - x0;
                if (0..4).contains(&size) {
                    // (x0 >= left and x0 + size <= right, key < keys: inside last)
                    unsafe { *last.add((((x0 - left) * keys + k) * 4 + size) as usize) = j };
                }
                j += 1;
            }
        }
    }
    let room = out.sh[1];
    let (ra, rest) = out.d.split_at_mut(room);
    let (rb, rest) = rest.split_at_mut(room);
    let (rc, rest) = rest.split_at_mut(room);
    let (rd_, re) = rest.split_at_mut(room);
    let mut m = 0usize;
    j = 0;
    for &(lo, hi) in &ranges {
        for i in lo.max(0) as usize..hi.max(0) as usize {
            if let Some((x0, x1)) = place(i) {
                let size = x1 - x0;
                j += 1;
                let k = rd(key, i) as i64;
                if (0..4).contains(&size)
                    && unsafe { *last.add((((x0 - left) * keys + k) * 4 + size) as usize) } != j - 1 {
                    continue;
                }
                wr(ra, m, x0);
                wr(rb, m, rd(row0, k as usize));
                wr(rc, m, imax(x1 - 1, x0));
                wr(rd_, m, rd(row1, k as usize) - 1);
                wr(re, m, rd(color, i) as i64);
                m += 1;
            }
        }
    }
    m as i64
}

#[allow(clippy::too_many_arguments)]
#[inline(never)] // (rare odd inputs: kept out of the fast loop's code)
fn order_rects_checked(s: &[i64], e: &[i64], key: &[i16], color: &[i32], first: &[i64], b0: i64, b1: i64, t_lo: f64,
                       t_hi: f64, ax: f64, bx: f64, kb: f64, w: f64, shown: &[Flag], clip: bool, c0: f64, c2: f64,
                       left: i64, right: i64, keys: i64, row0: &[i64], row1: &[i64], out: NdMut<i64, 2>) -> i64 {
    let nb = (b1 - b0).max(0) as usize;
    let mut ranges = need!(room(nb));
    for r in b0..b1 {
        ranges.push(block_range(s, first[wrap(r, first.len())], first[wrap(r + 1, first.len())], t_lo, t_hi));
    }
    let size_all = (right - left + 1) * keys * 4;
    let mut last: Vec<i64> = match try_zeros(size_all.max(0) as usize) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let nl = last.len();
    let mut j = 0i64;
    for &(lo, hi) in &ranges {
        for i in lo..hi {
            let i = wrap(i, s.len());
            if let Some((a, b)) = on_screen(s[i], e[i], key[i], ax, bx, kb, w, shown, clip, c0, c2) {
                let (x0, x1) = (imax(f2i(a), left), imin(f2i(b), right));
                let size = x1 - x0;
                if (0..4).contains(&size) {
                    last[wrap(((x0 - left) * keys + key[i] as i64) * 4 + size, nl)] = j;
                }
                j += 1;
            }
        }
    }
    let room = out.sh[1];
    let (ra, rest) = out.d.split_at_mut(room);
    let (rb, rest) = rest.split_at_mut(room);
    let (rc, rest) = rest.split_at_mut(room);
    let (rd, re) = rest.split_at_mut(room);
    let mut m = 0usize;
    j = 0;
    for &(lo, hi) in &ranges {
        for i in lo..hi {
            let i = wrap(i, s.len());
            if let Some((a, b)) = on_screen(s[i], e[i], key[i], ax, bx, kb, w, shown, clip, c0, c2) {
                let (x0, x1) = (imax(f2i(a), left), imin(f2i(b), right));
                let size = x1 - x0;
                j += 1;
                let k = key[i] as i64;
                if (0..4).contains(&size) && last[wrap(((x0 - left) * keys + k) * 4 + size, nl)] != j - 1 {
                    continue;
                }
                ra[m] = x0;
                rb[m] = row0[wrap(k, row0.len())];
                rc[m] = imax(x1 - 1, x0);
                rd[m] = row1[wrap(k, row1.len())] - 1;
                re[m] = color[i] as i64;
                m += 1;
            }
        }
    }
    m as i64
}

/// fastloops.last_on_pixels (x0 / x1 clamped in place) into out (5 rows, room len(x0)) -> how many.
/// Shared out over the pool's threads when there are many notes: the clamp and the last note per pixel (the
/// largest index wins: atomic max, the same as the serial last write), then the kept notes counted per chunk and
/// each chunk put at its own place (in order, so the same rows as one chunk).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn last_on_pixels(x0: &mut [i64], x1: &mut [i64], key: &[i64], color: &[i64], left: i64, right: i64, keys: i64,
                      row0: &[i64], row1: &[i64], out: NdMut<i64, 2>) -> i64 {
    let n = x0.len();
    let size_all = (right - left + 1) * keys * 4;
    let mut last: Vec<i64> = match try_zeros(size_all.max(0) as usize) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let nl = last.len();
    let room = out.sh[1];
    if x1.len() < n || key.len() < n || color.len() < n || out.d.len() < 5 * room {
        return crate::FAILED;
    }
    let pool = crate::pool_for(true, n, 2);
    let parallel = pool.is_some();
    let (size, chunks) = chunking(pool, n, 1);
    let (px0, px1, lp) = (Raw(x0.as_mut_ptr()), Raw(x1.as_mut_ptr()), Raw(last.as_mut_ptr()));
    // (1: the clamp in place, and each pixel's last note; the checks of the indices come here, once)
    let fine = each(pool, chunks, |c| {
        let (x0p, x1p, lpp) = (px0.get(), px1.get(), lp.get());
        for i in span(c, size, n) {
            unsafe {
                let a = imax(*x0p.add(i), left);
                let b = imin(*x1p.add(i), right);
                *x0p.add(i) = a;
                *x1p.add(i) = b;
                let sz = b - a;
                if (0..4).contains(&sz) {
                    let idx = wrap(((a - left) * keys + rd(key, i)) * 4 + sz, nl);
                    if idx >= nl {
                        return false;
                    }
                    if parallel {
                        (*(lpp.add(idx) as *const AtomicI64)).fetch_max(i as i64, Ordering::Relaxed);
                    } else {
                        *lpp.add(idx) = i as i64;
                    }
                }
            }
        }
        true
    });
    if fine.iter().any(|&ok| !ok) {
        return crate::FAILED;
    }
    let x0: &[i64] = x0;
    let x1: &[i64] = x1;
    let last: &[i64] = &last;
    // (2: the kept notes per chunk)
    let kept = each(pool, chunks, |c| {
        let mut cnt = 0i64;
        for i in span(c, size, n) {
            let (a, b) = (rd(x0, i), rd(x1, i));
            let sz = b - a;
            if (0..4).contains(&sz) && last[wrap(((a - left) * keys + rd(key, i)) * 4 + sz, nl)] != i as i64 {
                continue;
            }
            cnt += 1;
        }
        cnt
    });
    let mut offs_of: Vec<i64> = Vec::with_capacity(chunks);
    let mut total = 0i64;
    for &k in &kept {
        offs_of.push(total);
        total += k;
    }
    if total as usize > room {
        return crate::FAILED;
    }
    // (3: each chunk writes its own kept notes, from its own place on)
    let base = Raw(out.d.as_mut_ptr());
    each(pool, chunks, |c| {
        let bp = base.get();
        let mut p = offs_of[c] as usize;
        for i in span(c, size, n) {
            let (a, b) = (rd(x0, i), rd(x1, i));
            let sz = b - a;
            let k = rd(key, i);
            if (0..4).contains(&sz) && last[wrap(((a - left) * keys + k) * 4 + sz, nl)] != i as i64 {
                continue;
            }
            unsafe {
                // (p < total <= room: each chunk's places are its own)
                *bp.add(p) = a;
                *bp.add(room + p) = row0[wrap(k, row0.len())];
                *bp.add(2 * room + p) = imax(b - 1, a);
                *bp.add(3 * room + p) = row1[wrap(k, row1.len())] - 1;
                *bp.add(4 * room + p) = color[i];
            }
            p += 1;
        }
    });
    total
}

/// A raw pointer the pool's threads share: each chunk only touches its own places (counted before).
#[derive(Clone, Copy)]
struct Raw<T>(*mut T);
unsafe impl<T> Send for Raw<T> {}
unsafe impl<T> Sync for Raw<T> {}
impl<T> Raw<T> {
    fn get(self) -> *mut T {
        self.0
    }
}

/// (chunk size, chunks) for n items with `cells` numbers kept per chunk: the pool's threads x 4 (one chunk without
/// a pool), fewer when the cells would be many.
fn chunking(pool: Option<&rayon::ThreadPool>, n: usize, cells: usize) -> (usize, usize) {
    let want = pool.map_or(1, |p| 4 * p.current_num_threads()).min(n.max(1)).min(((1 << 22) / cells.max(1)).max(1));
    let want = want.max(1);
    let size = ((n + want - 1) / want).max(1);
    (size, ((n + size - 1) / size).max(1))
}

/// The items of chunk c (of `size` each, n items).
fn span(c: usize, size: usize, n: usize) -> std::ops::Range<usize> {
    (c * size).min(n)..((c + 1) * size).min(n)
}

/// f for every chunk (in the pool's threads when there is one), the results in chunk order.
fn each<T: Send>(pool: Option<&rayon::ThreadPool>, chunks: usize, f: impl Fn(usize) -> T + Sync + Send) -> Vec<T> {
    match pool {
        None => (0..chunks).map(f).collect(),
        Some(p) => p.install(move || (0..chunks).into_par_iter().map(f).collect()),
    }
}

/// fastloops.liquify_rects: one shape's notes on screen (rows `on`, start order) put in painting order by their
/// slot's rank (a stable counting sort; no ranks = as they are), with their colour numbers: x0 / x1 / key / code
/// into out (4 rows, room len(on)) -> 0. Shared out over the pool's threads when there are many notes: each chunk
/// of notes counts its ranks, the counts give every chunk its places (the same places as one chunk), then each
/// chunk puts its notes there.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn liquify_rects(on: &[i64], slot: &[i64], rank: &[i64], colour: &[i64], x0: &[i64], x1: &[i64], key: &[i64],
                     vq: &[i64], out: NdMut<i64, 2>) -> i64 {
    let n = on.len();
    if x0.len() < n || x1.len() < n || key.len() < n || vq.len() < n || out.sh[0] < 4 || out.sh[1] < n
        || out.d.len() < 4 * out.sh[1]
    {
        return crate::FAILED;
    }
    let ranked = !rank.is_empty();
    let pool = crate::pool_for(true, n, 2);
    // (every place checked once, the biggest rank found)
    let (size, chunks) = chunking(pool, n, 1);
    let checked = each(pool, chunks, |c| -> Option<i64> {
        let mut most = 0i64;
        for &r in &on[span(c, size, n)] {
            if r < 0 || r as usize >= slot.len() {
                return None;
            }
            let s = slot[r as usize];
            if s < 0 || s as usize >= colour.len() || (ranked && s as usize >= rank.len()) {
                return None;
            }
            if ranked {
                let k = rank[s as usize];
                if !(0..1 << 20).contains(&k) {
                    return None;
                }
                most = most.max(k);
            }
        }
        Some(most)
    });
    let mut found = 0i64;
    for m in checked {
        match m {
            Some(v) => found = found.max(v),
            None => return crate::FAILED,
        }
    }
    // (the places: one level per rank, or one level for all; counts per chunk and level, then running sums)
    let levels = if ranked && found > 0 { found as usize + 1 } else { 1 };
    let (size, chunks) = chunking(pool, n, levels);
    let mut at = need!(zeros::<i64>(levels * chunks));
    if levels > 1 {
        let cnt = Raw(at.as_mut_ptr());
        each(pool, chunks, |c| {
            let row = unsafe { std::slice::from_raw_parts_mut(cnt.get().add(c * levels), levels) };
            for &r in &on[span(c, size, n)] {
                row[rank[slot[r as usize] as usize] as usize] += 1;
            }
        });
    } else {
        for c in 0..chunks {
            at[c] = span(c, size, n).len() as i64;
        }
    }
    let mut running = 0i64;
    for k in 0..levels {
        for c in 0..chunks {
            let t = at[c * levels + k];
            at[c * levels + k] = running;
            running += t;
        }
    }
    let room = out.sh[1];
    let cnt = Raw(at.as_mut_ptr());
    let base = Raw(out.d.as_mut_ptr());
    each(pool, chunks, |c| {
        let row = unsafe { std::slice::from_raw_parts_mut(cnt.get().add(c * levels), levels) };
        let bp = base.get();
        for i in span(c, size, n) {
            let s = rd(slot, rd(on, i) as usize) as usize;
            let k = if levels > 1 { rd(rank, s) as usize } else { 0 };
            let p = row[k] as usize;
            row[k] += 1;
            // (p < n <= room: the counts put every note in its own place)
            unsafe {
                *bp.add(p) = rd(x0, i);
                *bp.add(room + p) = rd(x1, i);
                *bp.add(2 * room + p) = rd(key, i);
                *bp.add(3 * room + p) = rd(colour, s) * 32 + rd(vq, i);
            }
        }
    });
    0
}

/// fastloops.note_top. -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn note_top(top_px: &mut [i64], half: &mut [u8], kb: i64, top: i64, w: i64, h: i64, x0: &[i64], y0: &[i64],
                x1: &[i64], y1: &[i64], color: &[i64], shade: bool) -> i64 {
    let iw = w - kb;
    let (nt, nh) = (top_px.len(), half.len());
    for k in 0..x0.len() {
        let (a, b) = (imax(y0[k], top), imin(y1[k] + 1, h));
        let (c, d) = (imax(x0[k], kb), imin(x1[k] + 1, w));
        if b <= a || d <= c {
            continue;
        }
        let solid = x1[k] - x0[k] < 2 || y1[k] - y0[k] < 2;
        let (left, right) = (c == x0[k], d == x1[k] + 1);
        let end = right && (color[k] & NO_END) == 0;
        let (fill, edge) = (2 * k as i64, 2 * k as i64 + 1);
        for y in a..b {
            let row = (y - top) * iw - kb;
            let v = if solid || y == y0[k] || y == y1[k] { edge } else { fill };
            for x in c..d {
                let p = row + x;
                if shade {
                    let mine = if (left && x == c) || (end && x == d - 1) { 1u8 } else { 0 };
                    let hp = wrap(p, nh);
                    half[hp] = (if half[hp] != 0 { 2 } else { 0 }) | mine;
                }
                top_px[wrap(p, nt)] = v;
            }
            if v == fill {
                if left {
                    top_px[wrap(row + c, nt)] = edge;
                }
                if right {
                    top_px[wrap(row + d - 1, nt)] = edge;
                }
            }
        }
    }
    for v in half.iter_mut() {
        *v = if *v >= 2 { 1 } else { 0 };
    }
    0
}

/// fastloops.paint_notes. -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn paint_notes(img: NdMut<u8, 2>, kb: i64, top: i64, w: i64, h: i64, x0: &[i64], y0: &[i64], x1: &[i64],
                   y1: &[i64], color: &[i64], rgb: Nd<u8, 3>, shade: bool) -> i64 {
    let n = x0.len();
    let iw = w - kb;
    let nq = rgb.sh[0];
    // (checked once: a painted pixel is row (y - top) * iw + (x - kb) with y in top .. h and x in kb .. w (every
    // note is clipped to them first), so a picture of (h - top) * iw pixels holds them all; every colour inside
    // rgb; the lists as long as x0. Then no checks a pixel. Else the checked loop below)
    let total = (imax(h - top, 0) as i128) * (imax(iw, 0) as i128);
    let mask = NO_END - 1;
    if img.sh[1] != 3 || img.d.len() < img.sh[0] * 3 || (img.sh[0] as i128) < total || rgb.sh[1] < 2
        || rgb.sh[2] < 3 || rgb.d.len() < nq * rgb.sh[1] * rgb.sh[2] || y0.len() < n || x1.len() < n
        || y1.len() < n || color.len() < n || !color[..n].iter().fold(true, |ok, &c| ok & (((c & mask) as u64) < nq as u64)) {
        return paint_notes_checked(img, kb, top, w, h, x0, y0, x1, y1, color, rgb, shade);
    }
    let total = total as usize;
    let img = img.d;
    let mut flags: Vec<u8> = match try_zeros(if shade { total } else { 0 }) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let mut under: Vec<i64> = match try_zeros(imax(iw, 0) as usize) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    // (two copies of the loop: with / without the shading, so the plain one carries none of its work)
    if shade {
        paint_fast::<true>(img, kb, top, w, h, x0, y0, x1, y1, color, rgb, &mut flags, &mut under);
    } else {
        paint_fast::<false>(img, kb, top, w, h, x0, y0, x1, y1, color, rgb, &mut flags, &mut under);
    }
    0
}

/// A pixel run's 4-byte write (one instruction the compiler can't turn into vector code: on AVX-512 it made the
/// runs scatter writes, and its set-up per run cost more than the run; most notes are a few pixels wide).
#[inline(always)]
unsafe fn put4(q: *mut u8, word: u32) {
    #[cfg(target_arch = "x86_64")]
    std::arch::asm!("mov dword ptr [{0}], {1:e}", in(reg) q, in(reg) word, options(nostack, preserves_flags));
    #[cfg(not(target_arch = "x86_64"))]
    std::ptr::write_unaligned(q as *mut u32, word);
}

/// paint_notes' loop once its sizes were checked (no checks a pixel).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
fn paint_fast<const SHADE: bool>(img: &mut [u8], kb: i64, top: i64, w: i64, h: i64, x0: &[i64], y0: &[i64],
                                 x1: &[i64], y1: &[i64], color: &[i64], rgb: Nd<u8, 3>, flags: &mut [u8],
                                 under: &mut [i64]) {
    let n = x0.len();
    let iw = w - kb;
    let mask = NO_END - 1;
    let (rs, c3) = (rgb.sh[1] * rgb.sh[2], rgb.sh[2]);
    let px = |q: usize, j: usize, ch: usize| rd(rgb.d, q * rs + j * c3 + ch);
    // (p inside the picture: see paint_notes)
    let base = img.as_mut_ptr();
    let put = |p: i64, v: (u8, u8, u8)| unsafe {
        let q = base.add(p as usize * 3);
        *q = v.0;
        *q.add(1) = v.1;
        *q.add(2) = v.2;
    };
    // (a run of pixels p .. p + len: one 4-byte write a pixel, its 4th byte landing on the next pixel's red, which
    // the next write puts right; the last pixel three bytes, so nothing past the run changes)
    let run = |p: i64, len: i64, v: (u8, u8, u8)| unsafe {
        let word = u32::from_le_bytes([v.0, v.1, v.2, 0]);
        let mut q = base.add(p as usize * 3);
        for _ in 1..len {
            put4(q, word);
            q = q.add(3);
        }
        *q = v.0;
        *q.add(1) = v.1;
        *q.add(2) = v.2;
    };
    let shade = SHADE;
    for k in 0..n {
        let (xa, ya, xb, yb) = (rd(x0, k), rd(y0, k), rd(x1, k), rd(y1, k));
        let (a, b) = (imax(ya, top), imin(yb + 1, h));
        let (c, d) = (imax(xa, kb), imin(xb + 1, w));
        if b <= a || d <= c {
            continue;
        }
        let ck = rd(color, k);
        let solid = xb - xa < 2 || yb - ya < 2;
        let (left, right) = (c == xa, d == xb + 1);
        let end = right && (ck & NO_END) == 0;
        let q = (ck & mask) as usize;
        let f = (px(q, 0, 0), px(q, 0, 1), px(q, 0, 2));
        let ed = (px(q, 1, 0), px(q, 1, 1), px(q, 1, 2));
        let first = (a - top) * iw - kb;
        let mut m = 0usize;
        if shade {
            for x in first + c..first + d {
                let xi = x as usize;
                if rd(flags, xi) != 0 {
                    wr(under, m, x - first);
                    m += 1;
                    wr(flags, xi, 2);
                }
            }
            if left {
                let xi = (first + c) as usize;
                let v = rd(flags, xi) | 1;
                wr(flags, xi, v);
            }
            if end {
                let xi = (first + d - 1) as usize;
                let v = rd(flags, xi) | 1;
                wr(flags, xi, v);
            }
        }
        let mut sh = (0u8, 0u8, 0u8);
        if m > 0 {
            sh = (((f.0 as i64 + ed.0 as i64) / 2) as u8, ((f.1 as i64 + ed.1 as i64) / 2) as u8,
                  ((f.2 as i64 + ed.2 as i64) / 2) as u8);
        }
        for y in a..b {
            let row = (y - top) * iw - kb;
            if solid || y == ya || y == yb {
                run(row + c, d - c, ed);
            } else {
                run(row + c, d - c, f);
                for t in 0..m {
                    put(row + rd(under, t), sh);
                }
                if left {
                    put(row + c, ed);
                }
                if right {
                    put(row + d - 1, ed);
                }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
#[inline(never)] // (rare odd inputs: kept out of the fast loop's code)
fn paint_notes_checked(img: NdMut<u8, 2>, kb: i64, top: i64, w: i64, h: i64, x0: &[i64], y0: &[i64], x1: &[i64],
                       y1: &[i64], color: &[i64], rgb: Nd<u8, 3>, shade: bool) -> i64 {
    let iw = w - kb;
    let img = img.d;
    let ni = img.len() / 3;
    let rs = rgb.sh[1] * rgb.sh[2];
    let nq = rgb.sh[0];
    let fl = if shade { (imax(h - top, 0) * imax(iw, 0)) as usize } else { 0 };
    let mut flags: Vec<u8> = match try_zeros(fl) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let nf = flags.len();
    let mut under: Vec<i64> = match try_zeros(imax(iw, 0) as usize) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let c3 = rgb.sh[2];
    let px = |q: usize, j: usize, ch: usize| rgb.d[q * rs + j * c3 + ch];
    let mut put = |p: i64, v: (u8, u8, u8)| {
        let p = wrap(p, ni) * 3;
        img[p] = v.0;
        img[p + 1] = v.1;
        img[p + 2] = v.2;
    };
    for k in 0..x0.len() {
        let (a, b) = (imax(y0[k], top), imin(y1[k] + 1, h));
        let (c, d) = (imax(x0[k], kb), imin(x1[k] + 1, w));
        if b <= a || d <= c {
            continue;
        }
        let solid = x1[k] - x0[k] < 2 || y1[k] - y0[k] < 2;
        let (left, right) = (c == x0[k], d == x1[k] + 1);
        let end = right && (color[k] & NO_END) == 0;
        let q = wrap(color[k] & (NO_END - 1), nq);
        let f = (px(q, 0, 0), px(q, 0, 1), px(q, 0, 2));
        let ed = (px(q, 1, 0), px(q, 1, 1), px(q, 1, 2));
        let first = (a - top) * iw - kb;
        let mut m = 0usize;
        if shade {
            for x in first + c..first + d {
                let xi = wrap(x, nf);
                if flags[xi] != 0 {
                    under[m] = x - first;
                    m += 1;
                    flags[xi] = 2;
                }
            }
            if left {
                flags[wrap(first + c, nf)] |= 1;
            }
            if end {
                flags[wrap(first + d - 1, nf)] |= 1;
            }
        }
        let mut sh = (0u8, 0u8, 0u8);
        if m > 0 {
            sh = (((f.0 as i64 + ed.0 as i64) / 2) as u8, ((f.1 as i64 + ed.1 as i64) / 2) as u8,
                  ((f.2 as i64 + ed.2 as i64) / 2) as u8);
        }
        for y in a..b {
            let row = (y - top) * iw - kb;
            if solid || y == y0[k] || y == y1[k] {
                for x in c..d {
                    put(row + x, ed);
                }
            } else {
                for x in c..d {
                    put(row + x, f);
                }
                for &x in &under[..m] {
                    put(row + x, sh);
                }
                if left {
                    put(row + c, ed);
                }
                if right {
                    put(row + d - 1, ed);
                }
            }
        }
    }
    0
}
