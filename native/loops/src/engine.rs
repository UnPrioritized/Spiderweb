//! The note engine's loops (notes/engine.py, roll_draw.ring_parts, domino_clip): fastloops' overlap_order,
//! overlap_sweep, note_stretches, radix_order, ring_runs, clash_pairs, clash_levels. The same steps in the same
//! order as those, so the same rows come out.
use std::cell::RefCell;

use crate::maths::imax;
use crate::{try_zeros, Nd, NdMut, NO_MEMORY};

thread_local! {
    /// Results whose size isn't known before the loop runs (clash_pairs): kept here until take2 copies them out
    /// (the same thread asks right after; the GIL keeps two loops of one thread apart).
    pub(crate) static STASH: RefCell<Vec<Vec<i64>>> = const { RefCell::new(Vec::new()) };
}

/// The stash's first two lists into a and b (their lengths as the loop said) -> 0.
#[inline(always)]
pub fn take2(a: &mut [i64], b: &mut [i64]) -> i64 {
    STASH.with(|s| {
        let s = s.borrow();
        a.copy_from_slice(&s[0][..a.len()]);
        b.copy_from_slice(&s[1][..b.len()]);
    });
    STASH.with(|s| s.borrow_mut().clear());
    0
}

/// fastloops._merge_runs: order[a:b] sorted by start (notes' column 0), stable.
#[inline(always)]
fn merge_runs(nt: &[i64], nc: usize, order: &mut [i64], a: usize, b: usize) {
    let n = b - a;
    let mut idx: Vec<i64> = order[a..b].to_vec();
    let mut s: Vec<i64> = idx.iter().map(|&i| nt[i as usize * nc]).collect();
    let mut bounds = vec![0usize];
    for q in 1..n {
        if s[q] < s[q - 1] {
            bounds.push(q);
        }
    }
    bounds.push(n);
    let mut idx2 = vec![0i64; n];
    let mut s2 = vec![0i64; n];
    while bounds.len() > 2 {
        let mut new = vec![0usize];
        let mut r = 0;
        while r < bounds.len() - 1 {
            let lo = bounds[r];
            if r + 2 >= bounds.len() {
                let hi = bounds[r + 1];
                idx2[lo..hi].copy_from_slice(&idx[lo..hi]);
                s2[lo..hi].copy_from_slice(&s[lo..hi]);
                new.push(hi);
                r += 2;
                continue;
            }
            let (mid, hi) = (bounds[r + 1], bounds[r + 2]);
            let (mut i, mut j, mut o) = (lo, mid, lo);
            while i < mid && j < hi {
                if s[j] < s[i] {
                    idx2[o] = idx[j];
                    s2[o] = s[j];
                    j += 1;
                } else {
                    idx2[o] = idx[i];
                    s2[o] = s[i];
                    i += 1;
                }
                o += 1;
            }
            while i < mid {
                idx2[o] = idx[i];
                s2[o] = s[i];
                i += 1;
                o += 1;
            }
            while j < hi {
                idx2[o] = idx[j];
                s2[o] = s[j];
                j += 1;
                o += 1;
            }
            new.push(hi);
            r += 2;
        }
        bounds = new;
        std::mem::swap(&mut idx, &mut idx2);
        std::mem::swap(&mut s, &mut s2);
    }
    order[a..b].copy_from_slice(&idx);
}

/// fastloops.overlap_order into group / order (len(notes) each) -> 1, or 0 = the ids too spread out (Numba gives
/// two empty arrays then).
#[inline(always)]
pub fn overlap_order(notes: Nd<i64, 2>, group: &mut [i64], order: &mut [i64]) -> i64 {
    let n = notes.sh[0];
    let nc = notes.sh[1];
    let nt = notes.d;
    let gid = |i: usize| nt[i * nc + 4].wrapping_mul(256).wrapping_add(nt[i * nc + 2]);
    let (mut lo, mut hi) = (0i64, 0i64);
    for i in 0..n {
        let g = gid(i);
        if i == 0 || g < lo {
            lo = g;
        }
        if i == 0 || g > hi {
            hi = g;
        }
    }
    if n == 0 || lo < 0 || hi + 1 > 4 * n as i64 + 65536 {
        return 0;
    }
    let mut seen = vec![-1i64; (hi + 1) as usize];
    let mut first = vec![0i64; n + 1];
    let mut groups = 0i64;
    for i in 0..n {
        let g = gid(i) as usize;
        if seen[g] < 0 {
            seen[g] = groups;
            groups += 1;
        }
        group[i] = seen[g];
        first[seen[g] as usize + 1] += 1;
    }
    let ng = groups as usize;
    for g in 0..ng {
        first[g + 1] += first[g];
    }
    let mut at: Vec<i64> = first[..ng].to_vec();
    for i in 0..n {
        let g = group[i] as usize;
        order[at[g] as usize] = i as i64;
        at[g] += 1;
    }
    let col = |i: i64, c: usize| nt[i as usize * nc + c];
    for g in 0..ng {
        let (a, b) = (first[g] as usize, first[g + 1] as usize);
        for j in a + 1..b {
            if col(order[j], 0) < col(order[j - 1], 0) {
                merge_runs(nt, nc, order, a, b);
                break;
            }
        }
        let mut j = a;
        while j < b {
            let mut k = j + 1;
            while k < b && col(order[k], 0) == col(order[j], 0) {
                k += 1;
            }
            for p in j + 1..k {
                let v = order[p];
                let mut q = p as i64 - 1;
                while q >= j as i64 {
                    let u = order[q as usize];
                    if col(u, 3) > col(v, 3) || (col(u, 3) == col(v, 3) && col(u, 1) < col(v, 1)) {
                        order[q as usize + 1] = u;
                        q -= 1;
                    } else {
                        break;
                    }
                }
                order[(q + 1) as usize] = v;
            }
            j = k;
        }
    }
    1
}

/// fastloops.overlap_sweep into out ((len(order), notes' columns)) -> how many rows.
#[inline(always)]
pub fn overlap_sweep(notes: Nd<i64, 2>, order: &[i64], group: &[i64], out: NdMut<i64, 2>) -> i64 {
    let n = order.len();
    let nc = notes.sh[1];
    let nt = notes.d;
    let od = out.d;
    let oc = out.sh[1];
    let mut m = 0usize;
    let mut run = 0i64;
    for idx in 0..n {
        let i = order[idx] as usize;
        let (g, s, e) = (group[i], nt[i * nc], nt[i * nc + 1]);
        let mut end = e;
        if idx > 0 && group[order[idx - 1] as usize] == g {
            if s < run {
                end = imax(e, run);
            }
            run = imax(run, e);
        } else {
            run = e;
        }
        let mut last = true;
        if idx + 1 < n {
            let j = order[idx + 1] as usize;
            if group[j] == g {
                last = false;
                if nt[j * nc] < run {
                    end = nt[j * nc];
                }
            }
        }
        if last || end > s {
            od[m * oc..m * oc + nc].copy_from_slice(&nt[i * nc..i * nc + nc]);
            od[m * oc + 1] = end;
            m += 1;
        }
    }
    m as i64
}

/// fastloops.note_stretches into out ((len(notes), 4)) -> how many rows, or -10 = not in order (Numba: -1).
#[inline(always)]
pub fn note_stretches(notes: Nd<i64, 2>, by_key: bool, owner: i64, order: &[i64], out: NdMut<i64, 2>) -> i64 {
    let n = notes.sh[0];
    let nc = notes.sh[1];
    let nt = notes.d;
    let od = out.d;
    let col = |i: usize, c: usize| nt[i * nc + c];
    let mut m = 0usize;
    for i in 0..n {
        if col(i, 1) <= col(i, 0) {
            od[m * 4] = if by_key { col(i, 2) } else { 0 };
            od[m * 4 + 1] = col(i, 0);
            od[m * 4 + 2] = col(i, 1);
            od[m * 4 + 3] = owner;
            m += 1;
        }
    }
    let (first, mut pk, mut ps) = (m, 0i64, 0i64);
    let no = order.len();
    for j in 0..(if no > 0 { no } else { n }) {
        let i = if no > 0 { order[j] as usize } else { j };
        let (s, e) = (col(i, 0), col(i, 1));
        if e <= s {
            continue;
        }
        let k = if by_key { col(i, 2) } else { 0 };
        if m > first && no == 0 && (k < pk || k == pk && s < ps) {
            return -10;
        }
        pk = k;
        ps = s;
        if m > first && k == od[(m - 1) * 4] && s <= od[(m - 1) * 4 + 2] {
            if e > od[(m - 1) * 4 + 2] {
                od[(m - 1) * 4 + 2] = e;
            }
        } else {
            od[m * 4] = k;
            od[m * 4 + 1] = s;
            od[m * 4 + 2] = e;
            od[m * 4 + 3] = owner;
            m += 1;
        }
    }
    m as i64
}

/// fastloops.radix_order: k (work space, changed the same way), order (np.arange(n)), o2 (room n) -> 0 = the order
/// is in `order`, 1 = in `o2`.
#[inline(always)]
pub fn radix_order(k: &mut [i64], bits: i64, order: &mut [i64], o2: &mut [i64]) -> i64 {
    let n = k.len();
    let mut kb: Vec<i64> = match try_zeros(n) {
        Some(v) => v,
        None => return NO_MEMORY,
    };
    let mut count = [0i64; 2049];
    let mut shift = 0i64;
    let mut k_in_b = false; // (k1 = kb, else k)
    let mut o_in_2 = false; // (order = o2, else order)
    while shift < bits && n > 1 {
        count.iter_mut().for_each(|c| *c = 0);
        {
            let k1: &[i64] = if k_in_b { &kb } else { k };
            for &v in k1.iter() {
                count[((v >> shift) & 2047) as usize + 1] += 1;
            }
            if count[((k1[0] >> shift) & 2047) as usize + 1] == n as i64 {
                shift += 11;
                continue;
            }
        }
        for d in 0..2048 {
            count[d + 1] += count[d];
        }
        {
            let (k1, k2): (&[i64], &mut [i64]) = if k_in_b { (&kb, k) } else { (k, &mut kb) };
            let (src, dst): (&[i64], &mut [i64]) = if o_in_2 { (o2, order) } else { (order, o2) };
            for i in 0..n {
                let d = ((k1[i] >> shift) & 2047) as usize;
                let p = count[d] as usize;
                dst[p] = src[i];
                k2[p] = k1[i];
                count[d] = p as i64 + 1;
            }
        }
        o_in_2 = !o_in_2;
        k_in_b = !k_in_b;
        shift += 11;
    }
    o_in_2 as i64
}

/// fastloops.ring_runs into out (3 rows: key, start, end; room len(order)) -> how many runs (len(order) >= 1).
#[inline(always)]
pub fn ring_runs(s: &[i64], e: &[i64], k: &[i64], order: &[i64], gap: f64, big: i64, out: NdMut<i64, 2>) -> i64 {
    let n = order.len();
    let room = out.sh[1];
    let (rk, rest) = out.d.split_at_mut(room);
    let (ra, rb) = rest.split_at_mut(room);
    let mut m = 0usize;
    let i = order[0] as usize;
    let mut reach = e[i] + k[i] * big;
    rk[0] = k[i];
    ra[0] = s[i];
    for j in 1..n {
        let i = order[j] as usize;
        let at = s[i] + k[i] * big;
        if at as f64 > reach as f64 + gap {
            rb[m] = reach - rk[m] * big;
            m += 1;
            rk[m] = k[i];
            ra[m] = s[i];
        }
        reach = imax(reach, e[i] + k[i] * big);
    }
    rb[m] = reach - rk[m] * big;
    m as i64 + 1
}

/// ring_runs with a whole-number gap (Numba compares whole numbers then).
#[inline(always)]
pub fn ring_runs_int(s: &[i64], e: &[i64], k: &[i64], order: &[i64], gap: i64, big: i64, out: NdMut<i64, 2>) -> i64 {
    let n = order.len();
    let room = out.sh[1];
    let (rk, rest) = out.d.split_at_mut(room);
    let (ra, rb) = rest.split_at_mut(room);
    let mut m = 0usize;
    let i = order[0] as usize;
    let mut reach = e[i] + k[i] * big;
    rk[0] = k[i];
    ra[0] = s[i];
    for j in 1..n {
        let i = order[j] as usize;
        let at = s[i] + k[i] * big;
        if at > reach + gap {
            rb[m] = reach - rk[m] * big;
            m += 1;
            rk[m] = k[i];
            ra[m] = s[i];
        }
        reach = imax(reach, e[i] + k[i] * big);
    }
    rb[m] = reach - rk[m] * big;
    m as i64 + 1
}

/// fastloops.clash_pairs: the pairs stashed (take2) -> how many * 2 + 1, or 0 = the table would be too big (False).
#[inline(always)]
pub fn clash_pairs(items: Nd<i64, 2>, n: i64, most: i64) -> i64 {
    let rows = items.sh[0];
    let ic = items.sh[1];
    let it = items.d;
    let colv = |c: usize| -> Vec<i64> { (0..rows).map(|r| it[r * ic + c]).collect() };
    let (k, s, e, o) = (colv(0), colv(1), colv(2), colv(3));
    let mut order = vec![0i64; rows];
    if crate::order::key_order(&k, &s, &e, &o, &mut order, false) < 0 {
        return NO_MEMORY;
    }
    let nu = n as usize;
    let words = ((n + 63) >> 6) as usize;
    let lcap = (1usize << 20).min(16usize.max(4 * order.len()));
    let mut listed = vec![0i64; lcap];
    let mut nl = 0usize;
    let mut hit: Vec<u64> = Vec::new();
    let mut count: i64 = -1;
    let mut latest = vec![i64::MIN; nu];
    let mut on = vec![false; nu];
    let mut act = vec![0i64; nu];
    let mut na = 0usize;
    let m = order.len();
    let mut i = 0usize;
    while i < m {
        let key = k[order[i] as usize];
        while i < m && k[order[i] as usize] == key {
            let r = order[i] as usize;
            let (t, b) = (s[r], o[r]);
            let mut j = 0usize;
            for x in 0..na {
                let a = act[x];
                if latest[a as usize] > t {
                    act[j] = a;
                    j += 1;
                    if a != b {
                        let (lo, hi) = (a.min(b), a.max(b));
                        if count < 0 && nl < listed.len() {
                            listed[nl] = lo * n + hi;
                            nl += 1;
                        } else {
                            if count < 0 {
                                if n * words as i64 * 8 > most {
                                    return 0;
                                }
                                hit = match try_zeros::<u64>(nu * words) {
                                    Some(v) => v,
                                    None => return NO_MEMORY,
                                };
                                count = 0;
                                for &code in &listed[..nl] {
                                    let (p, q) = (code / n, code % n);
                                    let bit = 1u64 << (q & 63);
                                    let w = &mut hit[p as usize * words + (q >> 6) as usize];
                                    if *w & bit == 0 {
                                        *w |= bit;
                                        count += 1;
                                    }
                                }
                            }
                            let bit = 1u64 << (hi & 63);
                            let w = &mut hit[lo as usize * words + (hi >> 6) as usize];
                            if *w & bit == 0 {
                                *w |= bit;
                                count += 1;
                            }
                        }
                    }
                } else {
                    on[a as usize] = false;
                }
            }
            na = j;
            let bu = b as usize;
            if e[r] > latest[bu] {
                latest[bu] = e[r];
            }
            if !on[bu] && latest[bu] > t {
                on[bu] = true;
                act[na] = b;
                na += 1;
            }
            i += 1;
        }
        for x in 0..na {
            on[act[x] as usize] = false;
        }
        let mut x = i as i64 - 1;
        while x >= 0 {
            let r = order[x as usize] as usize;
            if k[r] != key {
                break;
            }
            latest[o[r] as usize] = i64::MIN;
            x -= 1;
        }
        na = 0;
    }
    let (pa, pb) = if count < 0 {
        let mut code = listed[..nl].to_vec();
        code.sort_unstable();
        code.dedup();
        (code.iter().map(|&c| crate::maths::fdiv(c, n)).collect::<Vec<i64>>(),
         code.iter().map(|&c| crate::maths::fmodi(c, n)).collect::<Vec<i64>>())
    } else {
        let mut pa = Vec::with_capacity(count as usize);
        let mut pb = Vec::with_capacity(count as usize);
        for a in 0..nu {
            for w in 0..words {
                let mut x = hit[a * words + w];
                let mut bit = 0i64;
                while x != 0 {
                    if x & 1 != 0 {
                        pa.push(a as i64);
                        pb.push(((w as i64) << 6) + bit);
                    }
                    x >>= 1;
                    bit += 1;
                }
            }
        }
        (pa, pb)
    };
    let c = pa.len() as i64;
    STASH.with(|st| {
        let mut st = st.borrow_mut();
        st.clear();
        st.push(pa);
        st.push(pb);
    });
    c * 2 + 1
}

/// fastloops._first_at: how many of arr[:n] (sorted) are below x, searched outwards from hint.
#[inline(always)]
fn first_at(arr: &[i64], n: i64, x: i64, hint: i64) -> i64 {
    let mut hint = hint;
    if hint > n {
        hint = n;
    }
    let (mut lo, mut hi);
    if hint < n && arr[hint as usize] < x {
        lo = hint + 1;
        let mut step = 1;
        hi = lo;
        while hi < n && arr[hi as usize] < x {
            lo = hi + 1;
            step *= 2;
            hi = lo + step - 1;
        }
        if hi > n {
            hi = n;
        }
    } else {
        hi = hint;
        let mut step = 1;
        lo = hi - 1;
        while lo > 0 && arr[lo as usize] >= x {
            hi = lo;
            step *= 2;
            lo = hi - step;
        }
        if lo < 0 {
            lo = 0;
        }
    }
    while lo < hi {
        let mid = (lo + hi) >> 1;
        if arr[mid as usize] < x {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    lo
}

/// fastloops.clash_levels (out filled) -> 0.
#[inline(always)]
pub fn clash_levels(rows: Nd<i64, 2>, offs: &[i64], out: &mut [i64]) -> i64 {
    let n = rows.sh[0];
    let rc = rows.sh[1];
    let rd = rows.d;
    let row = |i: usize, c: usize| rd[i * rc + c];
    if n == 0 || offs.len() < 3 {
        return 0;
    }
    let (mut kmin, mut kmax) = (row(0, 2), row(0, 2));
    for i in 0..n {
        let k = row(i, 2);
        if k < kmin {
            kmin = k;
        }
        if k > kmax {
            kmax = k;
        }
    }
    let nk = (kmax - kmin + 1) as usize;
    let mut at = vec![0i64; nk + 1];
    for i in 0..n {
        at[(row(i, 2) - kmin) as usize + 1] += 1;
    }
    for g in 0..nk {
        at[g + 1] += at[g];
    }
    let mut pos: Vec<i64> = at[..nk].to_vec();
    let mut order = vec![0i64; n];
    for i in 0..n {
        let g = (row(i, 2) - kmin) as usize;
        order[pos[g] as usize] = i as i64;
        pos[g] += 1;
    }
    let mut cs: Vec<Vec<i64>> = vec![Vec::new()];
    let mut ce: Vec<Vec<i64>> = vec![Vec::new()];
    let mut ss: Vec<Vec<i64>> = vec![Vec::new()];
    let mut se: Vec<Vec<i64>> = vec![Vec::new()];
    let mut cnt: Vec<i64> = vec![0; 16];
    let mut used;
    for g in 0..nk {
        let (a, b) = (at[g] as usize, at[g + 1] as usize);
        if b == a {
            continue;
        }
        used = 0usize;
        let mut i = a;
        while i < b {
            let mut l = 0usize;
            while offs[l + 1] <= order[i] {
                l += 1;
            }
            let end_list = offs[l + 1];
            let mut c = i;
            while c < b && order[c] < end_list {
                c += 1;
            }
            let mut bidx: Vec<i64> = order[i..c].to_vec();
            let m = c - i;
            let mut srt = true;
            for t in 1..m {
                if row(bidx[t] as usize, 0) < row(bidx[t - 1] as usize, 0) {
                    srt = false;
                    break;
                }
            }
            if !srt {
                bidx.sort_by_key(|&x| row(x as usize, 0)); // (stable, as np.argsort's mergesort)
            }
            let mut top = 0usize;
            let mut hint = vec![0i64; cnt.len()];
            for t in 0..m {
                let x = bidx[t] as usize;
                let (sx, ex) = (row(x, 0), row(x, 1));
                let mut lv = 0usize;
                while lv < used {
                    let lo = first_at(&cs[lv], cnt[lv], ex, hint[lv]);
                    hint[lv] = lo;
                    if lo > 0 && ce[lv][(lo - 1) as usize] > sx {
                        lv += 1;
                    } else {
                        break;
                    }
                }
                out[x] = lv as i64;
                if lv > top {
                    top = lv;
                }
            }
            for lv in 0..=top {
                while cs.len() <= lv {
                    cs.push(Vec::new());
                    ce.push(Vec::new());
                    ss.push(Vec::new());
                    se.push(Vec::new());
                }
                if cnt.len() <= lv {
                    cnt.resize(2 * lv + 16, 0);
                }
                let mut add = 0usize;
                for t in 0..m {
                    if out[bidx[t] as usize] == lv as i64 {
                        add += 1;
                    }
                }
                let q = if lv < used { cnt[lv] as usize } else { 0 };
                if q == 0 && add == 0 {
                    continue;
                }
                let need = q + add;
                let mut ns = std::mem::take(&mut ss[lv]);
                let mut ne = std::mem::take(&mut se[lv]);
                if ns.len() < need {
                    let size = need + (need >> 1) + 8;
                    ns = vec![0; size];
                    ne = vec![0; size];
                }
                let (old_s, old_e) = (&cs[lv], &ce[lv]);
                let (mut p, mut mm) = (0usize, 0usize);
                let mut run = -(1i64 << 62);
                for t in 0..m {
                    let x = bidx[t] as usize;
                    if out[x] != lv as i64 {
                        continue;
                    }
                    let sx = row(x, 0);
                    while p < q && old_s[p] <= sx {
                        if old_e[p] > run {
                            run = old_e[p];
                        }
                        ns[mm] = old_s[p];
                        ne[mm] = run;
                        p += 1;
                        mm += 1;
                    }
                    let ex = row(x, 1);
                    if ex > run {
                        run = ex;
                    }
                    ns[mm] = sx;
                    ne[mm] = run;
                    mm += 1;
                }
                while p < q {
                    if old_e[p] > run {
                        run = old_e[p];
                    }
                    ns[mm] = old_s[p];
                    ne[mm] = run;
                    p += 1;
                    mm += 1;
                }
                ss[lv] = std::mem::replace(&mut cs[lv], ns);
                se[lv] = std::mem::replace(&mut ce[lv], ne);
                cnt[lv] = need as i64;
            }
            if top + 1 > used {
                used = top + 1;
            }
            i = c;
        }
    }
    0
}
