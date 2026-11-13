//! Hz bass (hzbass.py, hz_grid.KeyGrid.made_fast): fastloops' wave_fill, note_ends, hz_expand, hz_late, hz_order,
//! hz_finish. The same sums in the same order as those (floats to whole numbers as Numba does them: f2i).
use crate::maths::{f2i, fdiv, fmodi, imax, imin, pmax, pmin, rint};
use crate::pyext::{Out, Outs};
use crate::{copied, filled, gathered, need, room, zeros, Flag, K_BOOL};

/// A place counted from the end when below 0 (Numba's a[-1]).
#[inline(always)]
fn wi(i: i64, n: usize) -> usize {
    if i < 0 {
        (i + n as i64) as usize
    } else {
        i as usize
    }
}

/// fastloops.wave_fill (out[a:b] filled) -> 0.
#[inline(always)]
pub fn wave_fill(starts: &[i64], a: i64, b: i64, sq0: &[i64], wo: &[f64], out: &mut [f64]) -> i64 {
    let m = sq0.len() as i64;
    let mut lo = 0i64;
    let mut prev = if a < b { starts[wi(a, starts.len())] } else { 0 };
    for i in a..b {
        let iu = wi(i, starts.len());
        let x = starts[iu];
        if x < prev {
            lo = 0;
        }
        prev = x;
        let mut steps = 0;
        while lo < m && sq0[lo as usize] <= x && steps < 8 {
            lo += 1;
            steps += 1;
        }
        if lo < m && sq0[lo as usize] <= x {
            let mut hi = m;
            while lo < hi {
                let mid = fdiv(lo + hi, 2);
                if sq0[mid as usize] <= x {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }
        }
        let at = (lo - 1).max(0);
        out[wi(i, out.len())] = wo[wi(at, wo.len())];
    }
    0
}

/// fastloops.bez_solve (hz_lines.bez_at one spot at a time, the same sums in the same order) into out -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn bez_solve(x: &[f64], x0: &[f64], c1x: &[f64], c2x: &[f64], x1: &[f64], v0: &[f64], c1v: &[f64], c2v: &[f64],
                 v1: &[f64], rounds: i64, out: &mut [f64]) -> i64 {
    let n = x.len();
    // (sizes checked once: a short one panics here, caught = the twin answers; no checks inside the loop)
    let (x0, c1x, c2x, x1) = (&x0[..n], &c1x[..n], &c2x[..n], &x1[..n]);
    let (v0, c1v, c2v, v1, out) = (&v0[..n], &c1v[..n], &c2v[..n], &v1[..n], &mut out[..n]);
    for i in 0..n {
        let (a0, a1, a2, a3) = (x0[i], c1x[i], c2x[i], x1[i]);
        let ax = a3 - a0 + 3.0 * (a1 - a2);
        let bx = 3.0 * (a0 - 2.0 * a1 + a2);
        let cx = 3.0 * (a1 - a0);
        let w = if a3 - a0 > 0.0 { a3 - a0 } else { 1.0 };
        let xi = x[i];
        let mut t = (xi - a0) / w;
        if t < 0.0 {
            t = 0.0;
        } else if t > 1.0 {
            t = 1.0;
        }
        let (mut lo, mut hi) = (0.0f64, 1.0f64);
        for _ in 0..rounds {
            let f = ((ax * t + bx) * t + cx) * t + a0 - xi;
            if f < 0.0 {
                lo = t;
            } else {
                hi = t;
            }
            let d = (3.0 * ax * t + 2.0 * bx) * t + cx;
            if d > 1e-300 {
                let step = t - f / d;
                if step >= lo && step <= hi {
                    t = step;
                    continue;
                }
            }
            t = (lo + hi) / 2.0;
        }
        let s = 1.0 - t;
        out[i] = s * s * s * v0[i] + 3.0 * s * s * t * c1v[i] + 3.0 * s * t * t * c2v[i] + t * t * t * v1[i];
    }
    0
}

/// fastloops.note_ends into res -> 0.
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn note_ends(s: &[i64], e: &[i64], k: &[i64], stick: bool, mode: i64, beats: f64, pct: f64, pct_int: bool,
                 wv: &[f64], wf: f64, big: i64, res: &mut [i64]) -> i64 {
    let n = s.len();
    let keyed: Vec<i64> = need!(gathered((0..n).map(|i| k[i].wrapping_mul(big).wrapping_add(s[i]))));
    let mut j = need!(zeros::<usize>(n));
    let mut i = 0;
    while i < n {
        let mut r = i + 1;
        while r < n && keyed[r] == keyed[i] {
            r += 1;
        }
        for t in i..r {
            j[t] = r;
        }
        i = r;
    }
    let mut room = need!(zeros::<i64>(n));
    let mut now = need!(zeros::<i64>(n));
    let mut last = need!(zeros::<bool>(n));
    for i in 0..n {
        now[i] = e[i] - s[i];
        let jj = j[i];
        if jj < n && k[jj] == k[i] {
            room[i] = s[jj] - s[i];
            last[i] = !(s[jj] <= e[i]);
        } else {
            room[i] = big;
            last[i] = true;
        }
    }
    let mut full = need!(copied(&now));
    for i in 1..n {
        if stick && last[i] && !last[i - 1] && k[i - 1] == k[i] {
            full[i] = imin(imax(now[i], now[i - 1]), room[i]);
        }
    }
    let mut chain = need!(zeros::<bool>(n));
    let mut group = need!(zeros::<usize>(n));
    if n > 0 {
        chain[0] = true;
    }
    for i in 1..n {
        chain[i] = k[i] != k[i - 1] || s[i] > s[i - 1] + full[i - 1];
        group[i] = group[i - 1] + chain[i] as usize;
    }
    let ng = if n > 0 { group[n - 1] + 1 } else { 0 };
    let mut gmax = need!(zeros::<i64>(ng));
    for i in 0..n {
        let v = s[i] + full[i];
        if chain[i] || v > gmax[group[i]] {
            gmax[group[i]] = v;
        }
    }
    let mut want = need!(zeros::<i64>(n));
    for i in 0..n {
        let ni = match mode {
            0 => imax(f2i(beats.floor()), 1),
            1 => {
                let mut w = wv[i];
                if w != w {
                    imax(full[i], 1)
                } else {
                    if w.is_infinite() {
                        w = f64::MAX.copysign(w);
                    }
                    imax(f2i((w * wf + 0.5).floor()), 1)
                }
            }
            2 => {
                if pct_int {
                    imax(f2i(((full[i].wrapping_mul(f2i(pct))) as f64 / 100.0 + 0.5).floor()), 1)
                } else {
                    imax(f2i((full[i] as f64 * pct / 100.0 + 0.5).floor()), 1)
                }
            }
            _ => imax(full[i], 1),
        };
        want[i] = imin(s[i].wrapping_add(ni), gmax[group[i]]);
    }
    let mut end = need!(zeros::<i64>(n));
    let mut reach = need!(zeros::<i64>(n));
    for i in 0..n {
        if chain[i] {
            reach[i] = want[i];
            end[i] = want[i];
        } else {
            reach[i] = imax(reach[i - 1], want[i]);
            end[i] = imax(want[i], reach[i - 1]);
        }
    }
    for i in 0..n.saturating_sub(1) {
        if !chain[i + 1] {
            end[i] = imin(end[i], s[i + 1]);
        }
    }
    for i in 0..n {
        res[i] = imax(end[i], s[i] + 1);
    }
    0
}

/// fastloops.hz_expand -> (part, number, src, waves, starts, run_p, ok).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn hz_expand(runs_sel: &[i64], copy_sel: &[i64], scale_c: &[f64], n_run: &[i64], off_run: &[i64],
                 prev_run: &[i64], moves_run: &[Flag], pos_len: i64, x: f64, off_pitch: f64, opf: &[f64], vf: &[f64],
                 w_: &[f64], s_: &[f64]) -> Outs {
    let r_ = runs_sel.len();
    let c_ = copy_sel.len();
    let p_ = r_ * c_;
    let mut n_p = need!(zeros::<i64>(p_));
    let mut run_p = need!(zeros::<i64>(p_));
    let mut scale_p = need!(zeros::<f64>(p_));
    for p in 0..p_ {
        run_p[p] = runs_sel[p / c_];
        n_p[p] = n_run[wi(run_p[p], n_run.len())];
        scale_p[p] = scale_c[p % c_];
    }
    let mut first = need!(zeros::<i64>(p_));
    let mut acc = 0i64;
    for p in 0..p_ {
        first[p] = acc;
        acc += n_p[p];
    }
    let m0 = acc.max(0) as usize;
    let mut part = need!(zeros::<i64>(m0));
    let mut src = need!(zeros::<i64>(m0));
    for p in 0..p_ {
        for k in 0..n_p[p].max(0) {
            let r = (first[p] + k) as usize;
            part[r] = p as i64;
            src[r] = off_run[wi(run_p[p], off_run.len())] + k;
        }
    }
    let mut stretch = need!(zeros::<f64>(m0));
    let mut inc = need!(zeros::<f64>(m0));
    let mut ok = true;
    for r in 0..m0 {
        let s = wi(src[r], opf.len());
        stretch[r] = scale_p[part[r] as usize] * (1.0 + off_pitch * opf[s] * (x - 0.5)) * vf[wi(src[r], vf.len())];
    }
    let mut moves = need!(zeros::<bool>(p_));
    for p in 0..p_ {
        let scaled = scale_p[p] != 1.0 && n_p[p] > 0;
        moves[p] = (moves_run[wi(run_p[p], moves_run.len())].on() || scaled) && n_p[p] > 0;
    }
    for r in 0..m0 {
        inc[r] = if moves[part[r] as usize] { 1.0 / stretch[r] } else { 1.0 };
        if !(inc[r] > 0.0 && inc[r] < f64::INFINITY) {
            ok = false;
        }
    }
    let mut total = need!(zeros::<f64>(p_));
    for r in 0..m0 {
        total[part[r] as usize] += inc[r];
    }
    let mut pos = need!(filled(pos_len.max(0) as usize, -1i64));
    for i in 0..r_ {
        let pl = pos.len();
        pos[wi(runs_sel[i], pl)] = i as i64;
    }
    let mut up = need!(zeros::<i64>(p_));
    for p in 0..p_ {
        let pr = prev_run[wi(run_p[p], prev_run.len())];
        let u = if pr >= 0 { pos[pr as usize] } else { -1 };
        up[p] = if u >= 0 { u * c_ as i64 + (p % c_) as i64 } else { -1 };
    }
    let mut head = need!(zeros::<f64>(p_));
    for p in 0..p_ {
        head[p] = if up[p] >= 0 { total[up[p] as usize] } else { 0.0 };
    }
    loop {
        if !up.iter().any(|&u| u >= 0) {
            break;
        }
        let mut nh = need!(zeros::<f64>(p_));
        let mut nu = need!(zeros::<i64>(p_));
        for p in 0..p_ {
            if up[p] >= 0 {
                let s = up[p] as usize;
                nh[p] = head[p] + head[s];
                nu[p] = up[s];
            } else {
                nh[p] = head[p] + 0.0;
                nu[p] = -1;
            }
        }
        head = nh;
        up = nu;
    }
    let mut counted = need!(zeros::<bool>(p_));
    let mut j0 = need!(zeros::<f64>(p_));
    let mut size = need!(zeros::<i64>(p_));
    for p in 0..p_ {
        let h = head[p];
        counted[p] = moves[p] || (h - rint(h)).abs() > 1e-9;
        j0[p] = (h - 1e-9).ceil();
        size[p] = if counted[p] { f2i(pmax((h + total[p] - 1e-9).ceil() - j0[p], 0.0)) } else { n_p[p] };
    }
    let mut ends = need!(zeros::<f64>(m0));
    let mut acc_f = 0.0f64;
    for r in 0..m0 {
        acc_f += inc[r];
        ends[r] = acc_f;
    }
    let mut excl = need!(zeros::<f64>(p_));
    acc_f = 0.0;
    for p in 0..p_ {
        excl[p] = acc_f;
        acc_f += total[p];
    }
    let mo: i64 = size.iter().sum();
    let mo = mo.max(0) as usize;
    let mut o_part = need!(zeros::<i64>(mo));
    let mut o_num = need!(zeros::<i64>(mo));
    let mut o_src = need!(zeros::<i64>(mo));
    let mut o_w = need!(zeros::<f64>(mo));
    let mut o_s = need!(zeros::<f64>(mo));
    let mut q = 0usize;
    for p in 0..p_ {
        if !counted[p] {
            let hr = f2i(rint(head[p]));
            for k in 0..n_p[p].max(0) {
                let s = src[(first[p] + k) as usize];
                o_part[q] = p as i64;
                o_num[q] = hr + k;
                o_src[q] = s;
                o_w[q] = w_[wi(s, w_.len())];
                o_s[q] = s_[wi(s, s_.len())];
                q += 1;
            }
        } else {
            let jb = f2i(j0[p]);
            let lo_b = first[p];
            let hi_b = first[p] + n_p[p] - 1;
            for t in 0..size[p] {
                let jj = jb + t;
                let here = excl[p] + (jj as f64 - head[p]);
                let (mut lo_i, mut hi_i) = (0i64, m0 as i64);
                while lo_i < hi_i {
                    let mid = fdiv(lo_i + hi_i, 2);
                    if here < ends[mid as usize] {
                        hi_i = mid;
                    } else {
                        lo_i = mid + 1;
                    }
                }
                let a = wi(imin(imax(lo_i, lo_b), hi_b), m0);
                let ia = inc[a];
                let s = src[a];
                let into = (here - (ends[a] - ia)) / ia;
                let ws = w_[wi(s, w_.len())];
                o_part[q] = p as i64;
                o_num[q] = jj;
                o_src[q] = s;
                o_w[q] = ws / ia;
                o_s[q] = s_[wi(s, s_.len())] + into * ws;
                q += 1;
            }
        }
    }
    Ok(vec![Out::I64(o_part, 0), Out::I64(o_num, 0), Out::I64(o_src, 0), Out::F64(o_w, 0), Out::F64(o_s, 0),
            Out::I64(run_p, 0), Out::Bool(ok)])
}

/// fastloops.hz_late -> (late, nsrc, noisy_p, place, total_took), with (part, number, src, waves, starts) first
/// when each repeat was copied once per trace hit (else the caller's own arrays stand).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn hz_late(part: &[i64], number: &[i64], src: &[i64], w: &[f64], s: &[f64], hits: &[f64], has_trace: bool, x: f64,
               slant: &[f64], groups: &[i64], noisy_f: &[f64], np_: i64) -> Outs {
    let m1 = part.len();
    let h = hits.len();
    let copied = has_trace && h != 1;
    let mut out = Vec::new();
    let (p2, s2): (std::borrow::Cow<[i64]>, std::borrow::Cow<[i64]>);
    if copied {
        let m2 = m1 * h;
        let mut pv = need!(zeros::<i64>(m2));
        let mut nv = need!(zeros::<i64>(m2));
        let mut sv = need!(zeros::<i64>(m2));
        let mut wv = need!(zeros::<f64>(m2));
        let mut stv = need!(zeros::<f64>(m2));
        for i in 0..m1 {
            for t in 0..h {
                let q = i * h + t;
                pv[q] = part[i];
                nv[q] = number[i];
                sv[q] = src[i];
                wv[q] = w[i];
                stv[q] = s[i];
            }
        }
        p2 = std::borrow::Cow::Owned(need!(crate::copied(&pv)));
        s2 = std::borrow::Cow::Owned(need!(crate::copied(&sv)));
        out.extend([Out::I64(pv, 0), Out::I64(nv, 0), Out::I64(sv, 0), Out::F64(wv, 0), Out::F64(stv, 0)]);
    } else {
        p2 = std::borrow::Cow::Borrowed(part);
        s2 = std::borrow::Cow::Borrowed(src);
    }
    let m2 = p2.len();
    let pn = np_.max(0) as usize;
    let mut late = need!(zeros::<f64>(m2));
    let mut nsrc = need!(zeros::<f64>(m2));
    let mut count = need!(zeros::<i64>(pn));
    let mut noisy_p = need!(zeros::<u8>(pn));
    for q in 0..m2 {
        let sq = wi(s2[q], slant.len());
        late[q] = if has_trace { hits[q % h] } else { 0.0 };
        let g = groups[sq] as f64;
        late[q] = late[q] + slant[sq] * x + (x * g).floor() / g;
        nsrc[q] = noisy_f[sq];
        let pq = wi(p2[q], pn);
        count[pq] += 1;
        if noisy_f[sq] != 0.0 {
            noisy_p[pq] = 1;
        }
    }
    let mut base = need!(zeros::<i64>(pn));
    let mut acc = 0i64;
    for p in 0..pn {
        base[p] = acc;
        acc += if noisy_p[p] != 0 { count[p] } else { 0 };
    }
    let total_took = acc;
    let mut excl_c = need!(zeros::<i64>(pn));
    acc = 0;
    for p in 0..pn {
        excl_c[p] = acc;
        acc += count[p];
    }
    let mut place = need!(zeros::<i64>(m2));
    for q in 0..m2 {
        let pq = wi(p2[q], pn);
        place[q] = base[pq] + q as i64 - excl_c[pq];
    }
    out.extend([Out::F64(late, 0), Out::F64(nsrc, 0), Out::U8(noisy_p, K_BOOL, 0), Out::I64(place, 0),
                Out::Int(total_took)]);
    Ok(out)
}

/// fastloops.hz_order (np.argsort(wg, kind="stable")).
#[inline(always)]
fn order_of(wg: &[i64]) -> Result<Vec<i64>, i64> {
    let k = wg.len();
    if k == 0 {
        return Ok(Vec::new());
    }
    let (mut mn, mut mx) = (wg[0], wg[0]);
    for &v in wg {
        if v < mn {
            mn = v;
        }
        if v > mx {
            mx = v;
        }
    }
    let span = mx.wrapping_sub(mn);
    if span >= 8589934592 || k < 256 {
        let mut order: Vec<i64> = gathered((0..k).map(|i| i as i64))?;
        // (stable as a merge sort: alike ones by their place, the last key; sort_unstable needs no extra memory)
        order.sort_unstable_by_key(|&i| (wg[i as usize], i));
        return Ok(order);
    }
    let key: Vec<i64> = gathered(wg.iter().map(|&v| v - mn))?;
    let mut order: Vec<i64> = gathered((0..k).map(|i| i as i64))?;
    let mut tmp = zeros::<i64>(k)?;
    let mut cnt = [0i64; 2049];
    let mut shift = 0;
    while (span >> shift) > 0 {
        cnt.iter_mut().for_each(|c| *c = 0);
        for &v in &key {
            cnt[((v >> shift) & 2047) as usize + 1] += 1;
        }
        for d in 0..2048 {
            cnt[d + 1] += cnt[d];
        }
        for i in 0..k {
            let d = ((key[order[i] as usize] >> shift) & 2047) as usize;
            tmp[cnt[d] as usize] = order[i];
            cnt[d] += 1;
        }
        std::mem::swap(&mut order, &mut tmp);
        shift += 11;
    }
    Ok(order)
}

/// fastloops.hz_order into out -> 0.
#[inline(always)]
pub fn hz_order(wg: &[i64], out: &mut [i64]) -> i64 {
    out[..wg.len()].copy_from_slice(&need!(order_of(wg)));
    0
}

/// lim_f's numbers as floats (Numba takes either kind there).
pub trait ToF: Copy {
    fn f(self) -> f64;
}
impl ToF for f64 {
    #[inline(always)]
    fn f(self) -> f64 {
        self
    }
}
impl ToF for i64 {
    #[inline(always)]
    fn f(self) -> f64 {
        self as f64
    }
}

/// fastloops.hz_finish -> (sq (n, 2), factor, mine, waves, K).
#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn hz_finish<L: ToF>(part: &[i64], number: &[i64], src: &[i64], w: &[f64], st0: &[f64], late: &[f64],
                         nsrc: &[f64], noisy_p: &[Flag], place: &[i64], drawn: &[f64], until_p: &[f64],
                         gains_sel: &[f64], has_loud: bool, has_wah: bool, hv: &[Flag], vol_f: &[f64], lev: &[f64],
                         oct_f: &[f64], own_f: &[f64], layer_f: &[f64], lim_f: &[L], loud_sw: &[f64], c1: &[f64],
                         tf: &[f64], soft_thr: f64) -> Outs {
    let m = part.len();
    let c = gains_sel.len() as i64;
    let mut st = need!(room(m));
    let mut li = need!(room(m));
    let mut wv = need!(room(m));
    let mut fa = need!(room(m));
    let mut pe0 = need!(room(m));
    let mut qu = need!(room(m));
    for q in 0..m {
        let p = part[q];
        let pu = wi(p, noisy_p.len());
        let s = src[q];
        let mut l = late[q];
        if noisy_p[pu].on() {
            l = l + nsrc[q] * drawn[wi(place[q], drawn.len())];
        }
        let s0 = st0[q] + l * w[q];
        if !(s0 < until_p[wi(p, until_p.len())] - 1e-6) {
            continue;
        }
        let su = s as usize; // (below 0 = a panic, caught: that call takes Numba's loop)
        let (env, quiet, soft);
        if has_loud {
            let mut lo = loud_sw[su];
            if has_wah {
                lo = (lo * c1[su]) / 2.0;
            }
            lo = lo * tf[su];
            let g = gains_sel[fmodi(p, c) as usize];
            let vol = (if hv[su].on() { vol_f[su] } else { 1.0 }) * (g * lev[su]);
            env = lo * vol;
            quiet = vol < soft_thr;
            soft = if fmodi(number[q], 2) == 1 { 1.0 - oct_f[su] } else { 1.0 };
        } else {
            env = 1.0;
            quiet = false;
            soft = 1.0;
        }
        let v0 = own_f[su];
        let v1 = layer_f[su];
        let mut f = env.sqrt() * soft * 1.0;
        f = f * (if v0 > 0.0 { v0 } else { 1.0 }) * v1;
        st.push(s0);
        li.push(lim_f[su].f());
        wv.push(w[q]);
        fa.push(f);
        pe0.push(v0);
        qu.push(quiet || v1 <= 0.0);
    }
    let k = st.len();
    if k == 0 {
        return Ok(vec![Out::F64(Vec::new(), 2), Out::F64(Vec::new(), 0), Out::U8(Vec::new(), K_BOOL, 0),
                       Out::F64(Vec::new(), 0), Out::Int(0)]);
    }
    let wg: Vec<i64> = gathered(st.iter().map(|&v| f2i((v + 0.5).floor())))?;
    let order = order_of(&wg)?;
    let mut gfirst = need!(room(k));
    let mut gstart = need!(room(k));
    let mut gmax = need!(room(k));
    let mut kk = 0usize;
    while kk < k {
        let a = kk;
        let i0 = order[a] as usize;
        let wcur = wg[i0];
        let mut mm = li[i0];
        kk += 1;
        while kk < k && wg[order[kk] as usize] == wcur {
            let v = li[order[kk] as usize];
            if v > mm {
                mm = v;
            }
            kk += 1;
        }
        gfirst.push(a);
        gstart.push(wcur);
        gmax.push(mm);
    }
    let ng = gstart.len();
    let mut out_sq = need!(room(ng * 2));
    let mut which = need!(room(ng));
    for g in 0..ng {
        let nxt = if g + 1 < ng { gstart[g + 1] as f64 } else { gmax[ng - 1] };
        let end = pmin(nxt, gmax[g]);
        let i = order[gfirst[g]] as usize;
        if end > gstart[g] as f64 && !qu[i] {
            out_sq.push(gstart[g] as f64);
            out_sq.push(end);
            which.push(i);
        }
    }
    let factor: Vec<f64> = gathered(which.iter().map(|&i| fa[i]))?;
    let mine: Vec<u8> = gathered(which.iter().map(|&i| (pe0[i] > 0.0) as u8))?;
    let wout: Vec<f64> = gathered(which.iter().map(|&i| wv[i]))?;
    Ok(vec![Out::F64(out_sq, 2), Out::F64(factor, 0), Out::U8(mine, K_BOOL, 0), Out::F64(wout, 0),
            Out::Int(k as i64)])
}

#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn hz_finish_f(part: &[i64], number: &[i64], src: &[i64], w: &[f64], st0: &[f64], late: &[f64], nsrc: &[f64],
                   noisy_p: &[Flag], place: &[i64], drawn: &[f64], until_p: &[f64], gains_sel: &[f64],
                   has_loud: bool, has_wah: bool, hv: &[Flag], vol_f: &[f64], lev: &[f64], oct_f: &[f64],
                   own_f: &[f64], layer_f: &[f64], lim_f: &[f64], loud_sw: &[f64], c1: &[f64], tf: &[f64],
                   soft_thr: f64) -> Outs {
    hz_finish(part, number, src, w, st0, late, nsrc, noisy_p, place, drawn, until_p, gains_sel, has_loud, has_wah, hv,
              vol_f, lev, oct_f, own_f, layer_f, lim_f, loud_sw, c1, tf, soft_thr)
}

#[allow(clippy::too_many_arguments)]
#[inline(always)]
pub fn hz_finish_i(part: &[i64], number: &[i64], src: &[i64], w: &[f64], st0: &[f64], late: &[f64], nsrc: &[f64],
                   noisy_p: &[Flag], place: &[i64], drawn: &[f64], until_p: &[f64], gains_sel: &[f64],
                   has_loud: bool, has_wah: bool, hv: &[Flag], vol_f: &[f64], lev: &[f64], oct_f: &[f64],
                   own_f: &[f64], layer_f: &[f64], lim_f: &[i64], loud_sw: &[f64], c1: &[f64], tf: &[f64],
                   soft_thr: f64) -> Outs {
    hz_finish(part, number, src, w, st0, late, nsrc, noisy_p, place, drawn, until_p, gains_sel, has_loud, has_wah, hv,
              vol_f, lev, oct_f, own_f, layer_f, lim_f, loud_sw, c1, tf, soft_thr)
}
