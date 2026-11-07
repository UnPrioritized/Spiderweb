//! The piano roll's note pictures (roll_draw): fastloops' paint_order, order_screen, order_rects, last_on_pixels,
//! note_top, paint_notes. The very same steps as those, in the same order, so the same pixels come out.
use crate::maths::{fdiv, fmodi, imax, imin, rint};
use crate::{try_zeros, Flag, Nd, NdMut, NO_MEMORY};

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
    let mut at: Vec<i64> = first[..nr].to_vec();
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
                x0[m] = a as i64;
                x1[m] = b as i64;
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
    let nb = (b1 - b0).max(0) as usize;
    let mut ranges = Vec::with_capacity(nb);
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
                let (x0, x1) = (imax(a as i64, left), imin(b as i64, right));
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
                let (x0, x1) = (imax(a as i64, left), imin(b as i64, right));
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
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn last_on_pixels(x0: &mut [i64], x1: &mut [i64], key: &[i64], color: &[i64], left: i64, right: i64, keys: i64,
                      row0: &[i64], row1: &[i64], out: NdMut<i64, 2>) -> i64 {
    let n = x0.len();
    for i in 0..n {
        x0[i] = imax(x0[i], left);
        x1[i] = imin(x1[i], right);
    }
    let size_all = (right - left + 1) * keys * 4;
    let mut last: Vec<i64> = match try_zeros(size_all.max(0) as usize) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let nl = last.len();
    for i in 0..n {
        let size = x1[i] - x0[i];
        if (0..4).contains(&size) {
            last[wrap(((x0[i] - left) * keys + key[i]) * 4 + size, nl)] = i as i64;
        }
    }
    let room = out.sh[1];
    let (ra, rest) = out.d.split_at_mut(room);
    let (rb, rest) = rest.split_at_mut(room);
    let (rc, rest) = rest.split_at_mut(room);
    let (rd, re) = rest.split_at_mut(room);
    let mut m = 0usize;
    for i in 0..n {
        let size = x1[i] - x0[i];
        if (0..4).contains(&size) && last[wrap(((x0[i] - left) * keys + key[i]) * 4 + size, nl)] != i as i64 {
            continue;
        }
        let k = key[i];
        ra[m] = x0[i];
        rb[m] = row0[wrap(k, row0.len())];
        rc[m] = imax(x1[i] - 1, x0[i]);
        rd[m] = row1[wrap(k, row1.len())] - 1;
        re[m] = color[i];
        m += 1;
    }
    m as i64
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
