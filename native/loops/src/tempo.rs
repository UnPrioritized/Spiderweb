//! Keep timing's file tempos (notes/tempo_file.py, tempo.py): fastloops' gap_ticks, gap_plan and the helpers they
//! call (_domino_safe1, _nearest1n, _pw_sum, _near_pair_n, _flat_pair_n, _full_pair_n, _fit_exact_n,
//! _fit_blocks_n, ...). The same sums in the same order, the same float32 rounding, as those.
use crate::maths::{f2i, fdiv, fmodi, imax, imin, pmax, pmin, rint};
use crate::pyext::{Out, Outs};

const TOP: i64 = 0xFFFFFF;

/// tempo.domino_safe of one value.
#[inline(always)]
fn domino_safe1(x: i64) -> bool {
    let bpm = (6e7 / imax(x, 1) as f64) as f32 as f64;
    f2i((6e7 / bpm).trunc()) == x
}

#[inline(always)]
fn safe1n(u: i64) -> bool {
    (1..=TOP).contains(&u) && domino_safe1(u)
}

#[inline(always)]
fn nearest1n(x: f64, reach: i64) -> i64 {
    let u = f2i(rint(x));
    if safe1n(u) {
        return u;
    }
    let s = if x >= u as f64 { 1 } else { -1 };
    for k in 1..reach {
        if safe1n(u + s * k) {
            return u + s * k;
        }
        if safe1n(u - s * k) {
            return u - s * k;
        }
    }
    u
}

/// NumPy's pairwise float64 sum of v[lo..lo + n] (fastloops._pw_sum).
fn pw_sum(v: &[f64], lo: usize, n: usize) -> f64 {
    if n <= 128 {
        return pw_leaf(v, lo, n);
    }
    let mut half = n / 2;
    half -= half % 8;
    pw_sum(v, lo, half) + pw_sum(v, lo + half, n - half)
}

#[inline(always)]
fn pw_leaf(v: &[f64], lo: usize, n: usize) -> f64 {
    if n < 8 {
        let mut res = 0.0;
        for i in 0..n {
            res += v[lo + i];
        }
        return res;
    }
    let mut r = [v[lo], v[lo + 1], v[lo + 2], v[lo + 3], v[lo + 4], v[lo + 5], v[lo + 6], v[lo + 7]];
    let mut i = 8;
    while i < n - (n % 8) {
        for (j, rj) in r.iter_mut().enumerate() {
            *rj += v[lo + i + j];
        }
        i += 8;
    }
    let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    while i < n {
        res += v[lo + i];
        i += 1;
    }
    res
}

#[inline(always)]
fn side_next(mut u: i64, way: i64) -> i64 {
    for _ in 0..64 {
        if safe1n(u) {
            return u;
        }
        u += way;
    }
    -1
}

/// tempo._near_pair: (0, a, ticks of a, b) found / (1, ...) None / (2, ...) far.
fn near_pair_n(n: i64, target: i64, reach: i64) -> (i64, i64, i64, i64) {
    let q = target as f64 / n as f64;
    let ru = reach.max(0) as usize;
    let mut downs = vec![0i64; ru];
    let mut ups = vec![0i64; ru];
    let v = side_next(f2i(q.floor()), -1);
    if v < 0 {
        return (2, 0, 0, 0);
    }
    downs[0] = v;
    let (mut nd, mut cur_d) = (1usize, v - 1);
    let v = side_next(f2i(q.ceil()), 1);
    if v < 0 {
        return (2, 0, 0, 0);
    }
    ups[0] = v;
    let (mut nu, mut cur_u) = (1usize, v + 1);
    let (mut found, mut b_far, mut b_ia, mut b_ib, mut b_a, mut b_b, mut b_k) = (false, 0.0f64, 0, 0, 0, 0, 0);
    let (mut ed, mut eu) = (0usize, 0usize);
    loop {
        for ia in 0..nd {
            for ib in 0..nu {
                if ia < ed && ib < eu {
                    continue;
                }
                let (av, bv) = (downs[ia], ups[ib]);
                if bv == av {
                    continue;
                }
                let gap = bv - av;
                let num = n * bv - target;
                let k = fdiv(num, gap);
                if fmodi(num, gap) != 0 || !(0 <= k && k <= n) {
                    continue;
                }
                let far = (q - av as f64) + (bv as f64 - q);
                if !found || far < b_far || (far == b_far && (ia < b_ia || (ia == b_ia && ib < b_ib))) {
                    (found, b_far, b_ia, b_ib, b_a, b_b, b_k) = (true, far, ia, ib, av, bv, k);
                }
            }
        }
        ed = nd;
        eu = nu;
        let limit = if !found { f64::INFINITY } else { b_far + 1e-6 };
        let grow_d = nd < ru && ((ups[0] - (downs[nd - 1] - 1)) as f64) <= limit;
        let grow_u = nu < ru && (((ups[nu - 1] + 1) - downs[0]) as f64) <= limit;
        if !grow_d && !grow_u {
            break;
        }
        if grow_d {
            let v = side_next(cur_d, -1);
            if v < 0 {
                return (2, 0, 0, 0);
            }
            downs[nd] = v;
            cur_d = v - 1;
            nd += 1;
        }
        if grow_u {
            let v = side_next(cur_u, 1);
            if v < 0 {
                return (2, 0, 0, 0);
            }
            ups[nu] = v;
            cur_u = v + 1;
            nu += 1;
        }
    }
    if !found {
        return (if nd >= ru && nu >= ru { 1 } else { 2 }, 0, 0, 0);
    }
    (0, b_a, b_k, b_b)
}

#[inline(always)]
fn next1n(u: i64, way: i64, reach: i64) -> i64 {
    for k in 1..reach {
        if safe1n(u + way * k) {
            return u + way * k;
        }
    }
    u
}

#[inline(always)]
fn next_small_n(u: i64, way: i64, reach: i64) -> i64 {
    let mut c = u + way;
    while (1..=TOP).contains(&c) && (c - u) * way < reach {
        let v = f2i((6e7 / ((6e7 / c as f64) as f32 as f64)).trunc());
        if v == c {
            return c;
        }
        c = if (v - c) * way > 0 { v } else { c + way };
    }
    u
}

fn next_safe_n(body: &[i64], nb: usize, way: i64, reach: i64, out: &mut [i64]) {
    if nb == 1 {
        out[0] = next1n(body[0], way, reach);
        return;
    }
    for i in 0..nb {
        let u = body[i];
        if nb <= 32 {
            out[i] = next_small_n(u, way, reach);
            continue;
        }
        let mut got = -1;
        for k in 1..9 {
            let c = u + way * k;
            if safe1n(c) {
                got = c;
                break;
            }
        }
        out[i] = if got != -1 { got } else { next_small_n(u, way, reach) };
    }
}

fn safe_side_n(start: i64, way: i64, reach: i64, safe_reach: i64, out: &mut [i64]) -> usize {
    let (mut c, mut u) = (0i64, start - way);
    while c < reach {
        let mut found = 0;
        let mut last = u;
        for k in 1..513 {
            let x = u + way * k;
            if (1..=TOP).contains(&x) && domino_safe1(x) {
                found += 1;
                if c < reach {
                    out[c as usize] = x;
                    c += 1;
                    last = x;
                }
            }
        }
        if found == 0 {
            let nxt = next1n(u, way, safe_reach);
            if nxt == u {
                break;
            }
            out[c as usize] = nxt;
            c += 1;
            u = nxt;
            continue;
        }
        u = last;
    }
    c as usize
}

fn full_pair_n(n: i64, target: i64, reach: i64, safe_reach: i64) -> (i64, i64, i64, i64) {
    let q = target as f64 / n as f64;
    let ru = reach.max(0) as usize;
    let mut downs = vec![0i64; ru];
    let mut ups = vec![0i64; ru];
    let nd = safe_side_n(f2i(q.floor()), -1, reach, safe_reach, &mut downs);
    let nu = safe_side_n(f2i(q.ceil()), 1, reach, safe_reach, &mut ups);
    if nd == 0 || nu == 0 {
        return (1, 0, 0, 0);
    }
    for ia in 0..nd {
        for ib in 0..nu {
            if ups[ib] == downs[ia] && downs[ia] * n == target {
                return (0, downs[ia], n, downs[ia]);
            }
        }
    }
    let (mut found, mut b_far, mut b_a, mut b_b, mut b_k) = (false, 0.0f64, 0, 0, 0);
    for ia in 0..nd {
        for ib in 0..nu {
            let (av, bv) = (downs[ia], ups[ib]);
            if bv == av {
                continue;
            }
            let num = n * bv - target;
            let k = fdiv(num, bv - av);
            if fmodi(num, bv - av) != 0 || !(0 <= k && k <= n) {
                continue;
            }
            let far = (q - av as f64) + (bv as f64 - q);
            if !found || far < b_far {
                (found, b_far, b_a, b_b, b_k) = (true, far, av, bv, k);
            }
        }
    }
    if !found {
        return (1, 0, 0, 0);
    }
    (0, b_a, b_k, b_b)
}

fn flat_pair_n(n: i64, target: i64, reach: i64, safe_reach: i64) -> (i64, i64, i64, i64) {
    let a0 = fdiv(target, n);
    let r = target - a0 * n;
    if r == 0 && safe1n(a0) {
        return (0, a0, n, a0);
    }
    if r != 0 && safe1n(a0) && safe1n(a0 + 1) {
        return (0, a0, n * (a0 + 1) - target, a0 + 1);
    }
    let got = near_pair_n(n, target, reach);
    if got.0 != 2 {
        return got;
    }
    full_pair_n(n, target, reach, safe_reach)
}

#[inline(always)]
fn put_pair(out: &mut [i64], n: i64, a: i64, ka: i64, b: i64) {
    for i in 0..n {
        out[i as usize] = if i < ka { a } else { b };
    }
}

/// tempo._fit_exact of ideal[:n] into out[:n]: 0 = made, 1 = None.
fn fit_exact_n(ideal: &[f64], n: i64, target: i64, safe_reach: i64, pair_reach: i64, out: &mut [i64]) -> i64 {
    if n == 0 || !(n <= target && target <= n * TOP) {
        return 1;
    }
    let nu = n as usize;
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for &v in &ideal[..nu] {
        lo = pmin(lo, v);
        hi = pmax(hi, v);
    }
    if hi - lo < 1.0 {
        let (code, pa, pk, pb) = flat_pair_n(n, target, pair_reach, safe_reach);
        if code == 0 {
            put_pair(out, n, pa, pk, pb);
            return 0;
        }
    }
    let scale = target as f64 / (0.0 + pw_sum(ideal, 0, nu));
    let mut want = vec![0f64; nu];
    for i in 0..nu {
        want[i] = ideal[i] * scale;
        out[i] = nearest1n(pmin(pmax(want[i], 1.0), TOP as f64), safe_reach);
    }
    let nb = imax(0, n - 2) as usize;
    let mut nxt = vec![0i64; nb.max(1)];
    for _ in 0..8 {
        let s: i64 = out[..nb].iter().sum();
        let rest = target - s - f2i(0.0 + pw_sum(&want, nb, nu - nb));
        let way = if rest > 0 { 1 } else { -1 };
        next_safe_n(out, nb, way, safe_reach, &mut nxt);
        let (mut used, mut took) = (0i64, 0usize);
        for i in 0..nb {
            used += (nxt[i] - out[i]).abs();
            if used > rest.abs() {
                break;
            }
            took += 1;
        }
        if took == 0 {
            break;
        }
        out[..took].copy_from_slice(&nxt[..took]);
    }
    let s: i64 = out[..nb].iter().sum();
    let rest = target - s;
    if n == 1 {
        out[0] = rest;
        return if (1..=TOP).contains(&rest) && domino_safe1(rest) { 0 } else { 1 };
    }
    let a0 = f2i(rint(rest as f64 / 2.0));
    for k in 0..safe_reach {
        for a in [a0 - k, a0 + k] {
            let b = rest - a;
            if safe1n(a) && safe1n(b) {
                out[nu - 2] = a;
                out[nu - 1] = b;
                return 0;
            }
        }
    }
    1
}

/// tempo._fit_blocks on file_plan's curved gap: 0 = out[:kj] made, 1 = None.
#[allow(clippy::too_many_arguments)]
fn fit_blocks_n(drawn: &[i64], a: i64, n0: i64, n1: i64, kj: i64, target: i64, every: i64, safe_reach: i64,
                pair_reach: i64, out: &mut [i64]) -> i64 {
    let n = kj;
    let nu = n.max(0) as usize;
    let mut ideal = vec![0f64; nu];
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for i in 0..nu {
        let x = drawn[(n0 - a + fdiv(i as i64 * (n1 - n0), kj)) as usize] as f64;
        ideal[i] = x;
        lo = pmin(lo, x);
        hi = pmax(hi, x);
    }
    if n <= every && every > 1 {
        if n <= target && target <= n * TOP {
            let (code, pa, pk, pb) = flat_pair_n(n, target, pair_reach, safe_reach);
            if code == 0 {
                put_pair(out, n, pa, pk, pb);
                return 0;
            }
        }
        return fit_exact_n(&ideal, n, target, safe_reach, pair_reach, out);
    }
    if every <= 1 || hi - lo < 1.0 {
        return fit_exact_n(&ideal, n, target, safe_reach, pair_reach, out);
    }
    if !(n <= target && target <= n * TOP) {
        return 1;
    }
    let nb = fdiv(n + every - 1, every) as usize;
    let eu = every as usize;
    let mut want = vec![0f64; nb];
    for b in 0..nb {
        let ln = imin(every, n - (b * eu) as i64);
        want[b] = ideal[b * eu] * ln as f64;
    }
    let scale = target as f64 / (0.0 + pw_sum(&want, 0, nb));
    for w in want.iter_mut() {
        *w = *w * scale;
    }
    let (mut done, mut owed) = (0i64, 0.0f64);
    for b in 0..nb - 1 {
        let x = (want[b] + owed) / every as f64;
        let u = nearest1n(pmin(pmax(x, 1.0), TOP as f64), safe_reach);
        for o in &mut out[b * eu..b * eu + eu] {
            *o = u;
        }
        owed += want[b] - (u * every) as f64;
        done += u * every;
    }
    let last = n - ((nb - 1) * eu) as i64;
    let rest = target - done;
    if !(last <= rest && rest <= last * TOP) {
        return fit_exact_n(&ideal, n, target, safe_reach, pair_reach, out);
    }
    let tail_at = (nb - 1) * eu;
    let (code, pa, pk, pb) = flat_pair_n(last, rest, pair_reach, safe_reach);
    if code == 0 {
        put_pair(&mut out[tail_at..], last, pa, pk, pb);
        return 0;
    }
    let flat = vec![rest as f64 / last as f64; last.max(0) as usize];
    if fit_exact_n(&flat, last, rest, safe_reach, pair_reach, &mut out[tail_at..]) == 0 {
        return 0;
    }
    fit_exact_n(&ideal, n, target, safe_reach, pair_reach, out)
}

#[inline(always)]
fn least1(t: i64, most: i64) -> i64 {
    let mut least = imax(1, -fdiv(-t, most));
    if least == 1 && t >= 2 && !domino_safe1(imin(imax(t, 1), most)) {
        least = 2;
    }
    least
}

/// fastloops.gap_plan; res = (j, last, pos) -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn gap_plan(j: i64, last: i64, a: i64, n: &[i64], total: &[i64], k: &mut [i64], drawn: &[i64], pref: &[i64],
                every: i64, most: i64, safe_reach: i64, pair_reach: i64, lens: &mut [i64], vals: &mut [i64], pos: i64,
                got: &mut [i64], res: &mut [i64]) -> i64 {
    let (mut j, mut last, mut pos) = (j, last, pos);
    let g = k.len() as i64;
    let put = |res: &mut [i64], j: i64, last: i64, pos: i64| {
        res[0] = j;
        res[1] = last;
        res[2] = pos;
        0
    };
    while j < g {
        let ju = j as usize;
        let (n0, n1) = (n[ju], n[ju + 1]);
        let (mut kj, tj) = (k[ju], total[ju]);
        if pos + kj + 10 > lens.len() as i64 {
            return put(res, j, last, pos);
        }
        let curved = kj > every && pref[(n1 - a - 1) as usize] - pref[(n0 - a) as usize] > 0;
        let least = least1(tj, most);
        let kk = imax(kj, 1);
        let qa = fdiv(tj, kk);
        let exact = fmodi(tj, kk) == 0;
        let qb = if exact { qa } else { qa + 1 };
        let qka = if exact { kj } else { kj * qb - tj };
        if !curved && kj >= 1 && qa >= 1 && qb <= most && least <= kj && kj <= tj
            && domino_safe1(imin(imax(qa, 1), most)) && domino_safe1(imin(imax(qb, 1), most)) {
            let p = pos as usize;
            if 0 < qka && qka < kj && last != -1 && qa != last && qb == last {
                lens[p] = kj - qka;
                vals[p] = qb;
                lens[p + 1] = qka;
                vals[p + 1] = qa;
                pos += 2;
                last = qa;
            } else {
                if qka > 0 {
                    lens[pos as usize] = qka;
                    vals[pos as usize] = qa;
                    pos += 1;
                }
                if kj - qka > 0 {
                    lens[pos as usize] = kj - qka;
                    vals[pos as usize] = qb;
                    pos += 1;
                }
                last = if qka < kj { qb } else { qa };
            }
            j += 1;
            continue;
        }
        let mut have = false;
        if curved {
            let code = fit_blocks_n(drawn, a, n0, n1, kj, tj, every, safe_reach, pair_reach, got);
            if code == 2 {
                return put(res, j, last, pos);
            }
            have = code == 0;
        }
        if !have {
            for step in 0..17i64 {
                let c = if step == 0 { kj } else { kj + fdiv(step + 1, 2) * if step % 2 != 0 { 1 } else { -1 } };
                if least <= c && c <= tj {
                    let (code, pa, pk, pb) = flat_pair_n(c, tj, pair_reach, safe_reach);
                    if code == 0 {
                        put_pair(got, c, pa, pk, pb);
                    } else {
                        let flat = vec![tj as f64 / c as f64; c.max(0) as usize];
                        if fit_exact_n(&flat, c, tj, safe_reach, pair_reach, got) != 0 {
                            continue;
                        }
                    }
                    kj = c;
                    k[ju] = c;
                    have = true;
                    break;
                }
            }
            if !have {
                let q = fdiv(tj, kj);
                let extra = tj - q * kj;
                for i in 0..kj {
                    got[i as usize] = if i < extra { q + 1 } else { q };
                }
            }
            let ku = kj as usize;
            if last != -1 && got[0] != last && got[ku - 1] == last {
                got[..ku].reverse();
            }
        }
        let ku = kj as usize;
        let mut start = 0usize;
        for i in 1..=ku {
            if i == ku || got[i] != got[i - 1] {
                lens[pos as usize] = (i - start) as i64;
                vals[pos as usize] = got[start];
                pos += 1;
                start = i;
            }
        }
        last = got[ku - 1];
        j += 1;
    }
    put(res, j, last, pos)
}

/// np.searchsorted(a, x, side) on sorted whole numbers.
#[inline(always)]
fn ss(a: &[i64], x: i64, right: bool) -> usize {
    let (mut lo, mut hi) = (0usize, a.len());
    while lo < hi {
        let m = (lo + hi) / 2;
        if if right { a[m] <= x } else { a[m] < x } {
            lo = m + 1;
        } else {
            hi = m;
        }
    }
    lo
}

/// fastloops.gap_ticks -> (n, total, k, dense).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn gap_ticks(a: i64, e: i64, ts: &[i64], bs: &[i64], drawn: &[i64], was: &[i64], u: i64, most: i64) -> Outs {
    let (i0, i1) = (ss(ts, a, false), ss(ts, e, true));
    let (b0, b1) = (ss(bs, a, true), ss(bs, e, false));
    let mut n = vec![0i64; 2 + i1.saturating_sub(i0) + b1.saturating_sub(b0)];
    n[0] = a;
    let mut c = 1usize;
    let (mut i, mut j) = (i0, b0);
    while i < i1 || j < b1 {
        let x;
        if j >= b1 || (i < i1 && ts[i] <= bs[j]) {
            x = ts[i];
            i += 1;
        } else {
            x = bs[j];
            j += 1;
        }
        if a < x && x < e && x != n[c - 1] {
            n[c] = x;
            c += 1;
        }
    }
    n[c] = e;
    c += 1;
    n.truncate(c);
    let mut note = vec![false; c];
    let mut jj = i0;
    for i in 0..c {
        while jj < i1 && ts[jj] < n[i] {
            jj += 1;
        }
        note[i] = jj < i1 && ts[jj] == n[i];
    }
    let span = (e - a).max(0) as usize;
    let mut sums = vec![0f64; span + 1];
    let mut s = 0.0;
    for i in 0..span {
        s += drawn[i] as f64;
        sums[i + 1] = s;
    }
    let g = c - 1;
    let mut total = vec![0i64; g];
    let mut k = vec![0i64; g];
    let mut dense = Vec::new();
    let (mut run, mut before) = (0.0f64, 0.0f64);
    let own = !was.is_empty();
    for i in 0..g {
        let (x0, x1) = ((n[i] - a) as usize, (n[i + 1] - a) as usize);
        let d = n[i + 1] - n[i];
        let tot = if own { was[x1] - was[x0] } else { d * u };
        let exact = tot as f64 / ((sums[x1] - sums[x0]) / d as f64);
        if note[i] && note[i + 1] && exact < 1.0 {
            dense.push(n[i]);
        }
        run += exact;
        let r = rint(run);
        let kk = f2i(r - before);
        before = r;
        let mut least = imax(1, -fdiv(-tot, most));
        if least == 1 && tot >= 2 && !domino_safe1(imin(imax(tot, 1), most)) {
            least = 2;
        }
        total[i] = tot;
        k[i] = imin(imax(kk, least), tot);
    }
    Ok(vec![Out::I64(n, 0), Out::I64(total, 0), Out::I64(k, 0), Out::I64(dense, 0)])
}
