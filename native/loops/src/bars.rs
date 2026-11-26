//! Fit bar lines on long songs (PLAN fit bar lines C, 2026-11-26): tempo_file._fit_bar_lines / _written_lines /
//! _signatures' bar-by-bar walks and _long_gaps' flat gaps. The very same numbers as that Python code, which is
//! their twin (not in NEEDS: no plain twin). Odd input = FAILED: the Python way does that call.
use crate::maths::{f2i, fdiv, fmodi, imax, imin};
use crate::tempo::{least1, pair_runs, ss};

/// tempo_file._fit_length: the bar length nearest d that time signatures can write (lengths sorted, not empty).
#[inline(always)]
fn fit_length(d: i64, lengths: &[i64]) -> i64 {
    let longest = lengths[lengths.len() - 1];
    let (mut d, mut base) = (d, 0i64);
    if d > longest {
        base = fdiv(d - 1, longest) * longest;
        d -= base;
    }
    let i = ss(lengths, d, false);
    if i < lengths.len() && lengths[i] == d {
        return base + d;
    }
    // (min by (distance, length): the shorter on a tie)
    let mut best = 0i64;
    let mut have = false;
    for j in [i as i64 - 1, i as i64] {
        if j >= 0 && (j as usize) < lengths.len() {
            let x = lengths[j as usize];
            if !have || (x - d).abs() < (best - d).abs() || ((x - d).abs() == (best - d).abs() && x < best) {
                best = x;
                have = true;
            }
        }
    }
    base + best
}

/// _fit_bar_lines' nudge(have) for one bar's g gaps (up / down their room, order = longest first): writes share
/// (the ticks each gap changes by) -> the ticks moved, or i64::MIN = None (share untouched).
#[inline(always)]
fn bar_nudge(have: i64, lengths: &[i64], up: &[i64], down: &[i64], order: &[i64], g: usize, share: &mut [i64])
             -> i64 {
    let i = ss(lengths, have, false);
    let lo = i.saturating_sub(3);
    let hi = (i + 3).min(lengths.len());
    // (sorted by distance, ties in their own order: Python's sorted is stable)
    let mut cand = [0i64; 6];
    let mut nc = 0usize;
    for &x in &lengths[lo..hi] {
        let mut p = nc;
        while p > 0 && (cand[p - 1] - have).abs() > (x - have).abs() {
            cand[p] = cand[p - 1];
            p -= 1;
        }
        cand[p] = x;
        nc += 1;
    }
    for &want in &cand[..nc] {
        let delta = want - have;
        if delta == 0 {
            return 0;
        }
        let cap = if delta > 0 { up } else { down };
        let mut sum = 0i64;
        let mut top = i64::MIN;
        for &o in &order[..g] {
            let c = cap[o as usize];
            sum += c;
            top = imax(top, c);
        }
        let left = delta.abs();
        if sum < left {
            continue;
        }
        // (water filling: the biggest L with sum(min(cap, L)) <= left, then one more each to the first gaps
        // (longest first) still with room)
        let (mut lo_l, mut hi_l) = (0i64, top);
        while lo_l < hi_l {
            let mid = fdiv(lo_l + hi_l + 1, 2);
            let mut s = 0i64;
            for &o in &order[..g] {
                s += imin(cap[o as usize], mid);
            }
            if s <= left {
                lo_l = mid;
            } else {
                hi_l = mid - 1;
            }
        }
        let mut got_sum = 0i64;
        for &o in &order[..g] {
            got_sum += imin(cap[o as usize], lo_l);
        }
        let mut rest = left - got_sum;
        for &o in &order[..g] {
            let c = cap[o as usize];
            let mut got = imin(c, lo_l);
            if rest > 0 && c > lo_l {
                got += 1;
                rest -= 1;
            }
            share[o as usize] = if delta > 0 { got } else { -got };
        }
        return delta;
    }
    i64::MIN
}

#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn fit_bars_inner(ms: &[i64], dk: &[i64], kk: &[i64], tt: &[i64], bars: &[i64], lengths: &[i64], nudge: f64,
                  most: i64, share: &mut [i64], up: &mut [i64], down: &mut [i64], order: &mut [i64],
                  state: &mut [i64]) {
    let n = ms.len();
    let (mut carry, mut last) = (state[0], state[1]);
    let mut lo = 0usize;
    while lo < n {
        let m = ms[lo];
        let mut hi = lo + 1;
        while hi < n && ms[hi] == m {
            hi += 1;
        }
        if carry != 0 && last >= 0 {  // (bars in between, no gaps to nudge: the carry goes on through them)
            for b in (last + 1) as usize..m as usize {
                let need = bars[b + 1] - bars[b] - carry;
                carry = fit_length(need, lengths) - need;
            }
        }
        last = m;
        let mu = m as usize;
        let mut true_len = bars[mu + 1] - bars[mu];
        for &x in &dk[lo..hi] {
            true_len += x;
        }
        let g = hi - lo;
        for i in 0..g {
            let (ks, tots) = (kk[lo + i], tt[lo + i]);
            let room = imax(1, f2i(ks as f64 * nudge));
            up[i] = imin(room, tots - ks);
            down[i] = imin(room, ks - least1(tots, most));
            order[i] = i as i64;
        }
        // (longest first, ties in their own order: np.argsort(-ks, kind="stable"))
        order[..g].sort_unstable_by_key(|&o| (-kk[lo + o as usize], o));
        let sh = &mut share[lo..hi];
        if bar_nudge(true_len - carry, lengths, &up[..g], &down[..g], &order[..g], g, sh) != i64::MIN {
            carry = 0;
            lo = hi;
            continue;
        }
        let moved_by = if carry != 0 {
            bar_nudge(true_len, lengths, &up[..g], &down[..g], &order[..g], g, sh)
        } else {
            i64::MIN
        };
        let need = true_len + if moved_by == i64::MIN { 0 } else { moved_by } - carry;
        carry = fit_length(need, lengths) - need;
        lo = hi;
    }
    state[0] = carry;
    state[1] = last;
}

#[inline(always)]
fn sorted_up(a: &[i64]) -> bool {
    a.windows(2).all(|w| w[0] < w[1])
}

/// tempo_file._fit_bar_lines' walk over the bars (gaps sorted by bar: ms; dk = ticks each one's file ticks differ
/// from its own, kk / tt its file ticks / time; share = zeros), a piece at a time (PLAN export in pieces): state =
/// [carry, last bar] from the piece before ([0, -1] at the start), handed on -> share = each gap's nudge; 0 or
/// FAILED (state untouched).
#[inline(always)]
#[allow(clippy::too_many_arguments)]
pub fn fit_bars_from(ms: &[i64], dk: &[i64], kk: &[i64], tt: &[i64], bars: &[i64], lengths: &[i64], nudge: f64,
                     most: i64, share: &mut [i64], up: &mut [i64], down: &mut [i64], order: &mut [i64],
                     state: &mut [i64]) -> i64 {
    let n = ms.len();
    if dk.len() != n || kk.len() != n || tt.len() != n || share.len() < n || up.len() < n || down.len() < n
        || order.len() < n || lengths.is_empty() || !sorted_up(lengths) || most < 1 || !nudge.is_finite()
        || state.len() < 2 || state[1] < -1 {
        return crate::FAILED;
    }
    for i in 0..n {
        let m = ms[i];
        if m < 0 || m as usize + 1 >= bars.len() || (i > 0 && ms[i - 1] > m) || m < state[1] {
            return crate::FAILED;
        }
    }
    fit_bars_inner(ms, dk, kk, tt, bars, lengths, nudge, most, share, up, down, order, state);
    0
}

/// tempo_file._written_lines (BAR CARRY-OVER): w = the written lines (a copy of fl made by Python), moved / unfit
/// = bar numbers, counts = [moved, unfit] -> 0 or FAILED.
#[inline(always)]
pub fn written_lines(fl: &[i64], lengths: &[i64], carry_most: i64, w: &mut [i64], moved: &mut [i64],
                     unfit: &mut [i64], counts: &mut [i64]) -> i64 {
    let nf = fl.len();
    if nf == 0 || w.len() < nf || moved.len() < nf || unfit.len() < nf || counts.len() < 2 || lengths.is_empty()
        || !sorted_up(lengths) || !(1..=64).contains(&carry_most) {
        return crate::FAILED;
    }
    let (mut nm, mut nu) = (0usize, 0usize);
    let last = nf - 1;
    let mut m = 0usize;
    let mut chain = [0i64; 64];
    let mut vals = [0i64; 64];
    while m < last {
        if fit_length(fl[m + 1] - w[m], lengths) == fl[m + 1] - w[m] {
            m += 1;
            continue;
        }
        let (mut k, mut prev, mut nchain, mut landed) = (m, w[m], 0usize, false);
        while k + 1 < last && (nchain as i64) < carry_most {
            prev += fit_length(fl[k + 1] - prev, lengths);
            vals[nchain] = prev;
            chain[nchain] = k as i64;
            nchain += 1;
            k += 1;
            if fit_length(fl[k + 1] - prev, lengths) == fl[k + 1] - prev {
                landed = true;
                break;
            }
        }
        if !landed {
            unfit[nu] = m as i64;
            nu += 1;
            m += 1;
            continue;
        }
        w[m + 1..m + 1 + nchain].copy_from_slice(&vals[..nchain]);
        moved[nm..nm + nchain].copy_from_slice(&chain[..nchain]);
        nm += nchain;
        m = k;
    }
    counts[0] = nm as i64;
    counts[1] = nu as i64;
    0
}

/// tempo_file.signature: (top, bottom power) for a bar this long, prefer's note value first (-1 = none);
/// (-1, -1) = None.
#[inline(always)]
fn signature_n(length: i64, ppq: i64, prefer: i64) -> (i64, i64) {
    let order = [prefer, 2, 3, 4, 5, 6, 7, 1, 0];
    let start = if prefer >= 0 { 0 } else { 1 };
    for &k in &order[start..] {
        let d = 1i64 << k;
        if fmodi(4 * ppq, d) != 0 {
            continue;
        }
        let unit = fdiv(4 * ppq, d);
        let q = fdiv(length, unit);
        if fmodi(length, unit) == 0 && (1..=255).contains(&q) {
            return (q, k);
        }
    }
    (-1, -1)
}

/// tempo_file._signatures' walk up to bar stop: lines = the written bar lines (file ticks), bars = Spiderweb's,
/// own = the project's signatures (starts, tops, powers) -> out rows (tick, top, power) + unfit (Spiderweb ticks),
/// counts = [rows, unfit, prev top, prev power] (the last written; -1 = none); 0 or FAILED.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
pub fn bar_sigs(lines: &[i64], bars: &[i64], stop: i64, own_s: &[i64], own_top: &[i64], own_pow: &[i64], ppq: i64,
                longest: i64, out: &mut [i64], unfit: &mut [i64], counts: &mut [i64]) -> i64 {
    let no = own_s.len();
    if stop < 0 || lines.len() < stop as usize + 2 || bars.len() < stop as usize + 2 || no == 0
        || own_top.len() != no || own_pow.len() != no || !(1..=(1 << 40)).contains(&ppq) || longest < 1
        || counts.len() < 4 || out.len() < 6 * (stop as usize + 1) || unfit.len() < stop as usize + 1 {
        return crate::FAILED;
    }
    for i in 0..no {
        if !(0..=7).contains(&own_pow[i]) || (i > 0 && own_s[i - 1] > own_s[i]) {
            return crate::FAILED;
        }
    }
    let (mut nr, mut nu) = (0usize, 0usize);
    let mut prev = (-1i64, -1i64);
    let ls = signature_n(longest, ppq, -1);
    for m in 0..=stop as usize {
        let (mut f0, f1) = (lines[m], lines[m + 1]);
        // (own_at: the project's signature in effect at this bar)
        let i = ss(own_s, bars[m], true);
        let o = if i == 0 { 0 } else { i - 1 };
        let mine = (own_top[o], own_pow[o]);
        let mut sig = if f1 - f0 == bars[m + 1] - bars[m] { mine } else { signature_n(f1 - f0, ppq, mine.1) };
        if sig.0 < 0 {  // (too long for one signature: as many of the longest as fit, then the rest)
            let (mut at, mut rest) = (f0, f1 - f0);
            if rest > longest {
                if ls != prev {
                    out[3 * nr] = at;
                    out[3 * nr + 1] = ls.0;
                    out[3 * nr + 2] = ls.1;
                    nr += 1;
                    prev = ls;
                }
                let skip = fdiv(rest - 1, longest) * longest;
                at += skip;
                rest -= skip;
            }
            sig = signature_n(rest, ppq, -1);
            if sig.0 < 0 {
                prev = (-1, -1);
                unfit[nu] = bars[m];
                nu += 1;
                continue;
            }
            f0 = at;
        }
        if sig != prev {
            out[3 * nr] = f0;
            out[3 * nr + 1] = sig.0;
            out[3 * nr + 2] = sig.1;
            nr += 1;
            prev = sig;
        }
    }
    counts[0] = nr as i64;
    counts[1] = nu as i64;
    counts[2] = prev.0;
    counts[3] = prev.1;
    0
}

#[inline(always)]
fn put_run(lens: &mut [i64], vals: &mut [i64], pos: &mut usize, len: i64, v: i64) {
    if len > 0 {
        lens[*pos] = len;
        vals[*pos] = v;
        *pos += 1;
    }
}

/// tempo_file._long_gaps' flat gaps from j on: stops at a curved one, or one whose numbers pair_runs can't take
/// (Python does it). k may change (a tick more / fewer); runs into lens / vals from pos; res = [j, last, pos]
/// (last -1 = none) -> 0 or FAILED.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
pub fn flat_gaps(j: i64, last: i64, total: &[i64], k: &mut [i64], least: &[i64], curved: &[u8], reach: i64,
                 safe_reach: i64, lens: &mut [i64], vals: &mut [i64], pos: i64, res: &mut [i64]) -> i64 {
    let g = k.len();
    if total.len() != g || least.len() != g || curved.len() != g || res.len() < 3 || j < 0 || pos < 0
        || lens.len() != vals.len() || pos as usize > lens.len() {
        return crate::FAILED;
    }
    let (mut j, mut last, mut pos) = (j as usize, last, pos as usize);
    let mut pr = [0i64; 3];
    // (pair_runs' answers remembered: a long part's bars ask the same (ticks, time) again and again; it depends on
    // nothing else, so the same answers. 64 places by (ticks, time); key ticks 0 = empty)
    let mut memo = [(0i64, 0i64, 0i64, [0i64; 3]); 64];
    'gaps: while j < g && curved[j] == 0 && pos + 2 <= lens.len() {
        let (mut kj, tj) = (k[j], total[j]);
        let mut pair = (0i64, 0i64, 0i64);
        let mut found = false;
        // (kj, then a tick more / fewer, up to 8 either way: the Python's order)
        for step in 0..17i64 {
            let c = if step == 0 { kj } else { kj + fdiv(step + 1, 2) * if step % 2 != 0 { 1 } else { -1 } };
            if least[j] <= c && c <= tj {
                let slot = ((c.wrapping_mul(0x9E37_79B9) ^ tj.wrapping_mul(0x85EB_CA6B)) as u64 >> 7) as usize & 63;
                let code = if c > 0 && memo[slot].0 == c && memo[slot].1 == tj {
                    pr = memo[slot].3;
                    memo[slot].2
                } else {
                    let code = pair_runs(c, tj, reach, safe_reach, &mut pr);
                    if c > 0 && code != crate::FAILED {
                        memo[slot] = (c, tj, code, pr);
                    }
                    code
                };
                if code == crate::FAILED {
                    break 'gaps;  // (past the compiled twin's reach: Python does this gap)
                }
                if code == 0 {
                    pair = (pr[0], pr[1], pr[2]);
                    kj = c;
                    found = true;
                    break;
                }
            }
        }
        if found {
            k[j] = kj;
        } else {
            if kj <= 0 {
                break;
            }
            pair = (fdiv(tj, kj), fdiv(tj, kj) + 1, kj - fmodi(tj, kj));
        }
        let (a, b, ka) = pair;
        if 0 < ka && ka < kj && last != -1 && a != last && b == last {
            put_run(lens, vals, &mut pos, kj - ka, b);
            put_run(lens, vals, &mut pos, ka, a);
            last = a;
        } else {
            put_run(lens, vals, &mut pos, ka, a);
            put_run(lens, vals, &mut pos, kj - ka, b);
            last = if ka < kj { b } else { a };
        }
        j += 1;
    }
    res[0] = j as i64;
    res[1] = last;
    res[2] = pos as i64;
    0
}
