//! Inside fills' distances to the outline (notes/shrink.py: Even band, Outline gate, Nested's field): fastloops'
//! band_rows, field_dist; Nested Blend's heat (notes/blend_rows.py): blend_heat. The same sums in the same order as
//! those.
use crate::maths::{pmax, pmin};
use rayon::prelude::*;

use crate::pyext::{Out, Outs};
use crate::{filled, grow, need, room, zeros, NdMut, FAILED};

/// fastloops._band_at: one edge's round-ended band on the line at y -> (lo, hi), lo >= hi = none.
#[inline(always)]
fn band_at(ax: f64, ay: f64, bx: f64, by: f64, length: f64, y: f64, r: f64) -> (f64, f64) {
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for (px, py) in [(ax, ay), (bx, by)] {
        let d = r * r - (y - py) * (y - py);
        if d > 0.0 {
            let w = d.sqrt();
            if px - w < lo {
                lo = px - w;
            }
            if px + w > hi {
                hi = px + w;
            }
        }
    }
    let (dx, dy) = (bx - ax, by - ay);
    let (mut b_lo, mut b_hi, mut ok);
    if dy.abs() < 1e-12 {
        b_lo = pmin(ax, bx);
        b_hi = pmax(ax, bx);
        ok = (y - ay).abs() < r;
    } else {
        let c = ax + dx * (y - ay) / dy;
        let w = r * length / dy.abs();
        b_lo = c - w;
        b_hi = c + w;
        ok = true;
    }
    if dx.abs() < 1e-12 {
        let t = (y - ay) * dy / (if length > 0.0 { length * length } else { 1.0 });
        ok = ok && t >= 0.0 && t <= 1.0;
    } else {
        let x0 = ax - (y - ay) * dy / dx;
        let x1 = x0 + length * length / dx;
        b_lo = pmax(b_lo, pmin(x0, x1));
        b_hi = pmin(b_hi, pmax(x0, x1));
    }
    if ok && b_hi > b_lo && length > 0.0 {
        if b_lo < lo {
            lo = b_lo;
        }
        if b_hi > hi {
            hi = b_hi;
        }
    }
    (lo, hi)
}

/// fastloops.band_rows -> how many joined stretches (line by line in row_out / lo_out / hi_out), FAILED = odd sizes.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
pub fn band_rows(ax: &[f64], ay: &[f64], bx: &[f64], by: &[f64], length: &[f64], ys: &[f64], first: &[i64],
                 n: &[i64], r: f64, row_out: &mut [i64], lo_out: &mut [f64], hi_out: &mut [f64]) -> i64 {
    let (ne, nl) = (ax.len(), ys.len());
    if ay.len() != ne || bx.len() != ne || by.len() != ne || length.len() != ne || first.len() != ne
        || n.len() != ne {
        return FAILED;
    }
    let mut total = 0usize;
    for e in 0..ne {
        let (f, k) = (first[e], n[e]);
        if f < 0 || k < 0 || f + k > nl as i64 {
            return FAILED;
        }
        total += k as usize;
    }
    if row_out.len() < total || lo_out.len() < total || hi_out.len() < total {
        return FAILED;
    }
    // each line's stretches together: counted, then placed (a line's room starts at count[row])
    let mut count: Vec<usize> = need!(zeros(nl + 1));
    for e in 0..ne {
        for row in first[e] as usize..(first[e] + n[e]) as usize {
            count[row + 1] += 1;
        }
    }
    for i in 0..nl {
        count[i + 1] += count[i];
    }
    // which edges reach each line (in edge order), so each line is worked out on its own
    let mut at: Vec<usize> = need!(zeros(nl));
    at.copy_from_slice(&count[..nl]);
    let mut ids: Vec<usize> = need!(zeros(total));
    for e in 0..ne {
        for row in first[e] as usize..(first[e] + n[e]) as usize {
            ids[at[row]] = e;
            at[row] += 1;
        }
    }
    let mut band: Vec<(f64, f64)> = need!(filled(total, (0.0, 0.0)));
    let mut kept: Vec<usize> = need!(zeros(nl));
    let d = Band { ax, ay, bx, by, length, ys, r, count: &count, ids: &ids };
    match crate::pool_for(true, total, 4) {
        None => d.lines(0, nl, &mut band, &mut kept),
        Some(pool) => {
            // (lines in groups of about the same number of edges; each group owns its lines' room)
            let groups = pool.current_num_threads() * 8;
            let mut parts = Vec::new();
            let (mut rest_b, mut rest_k) = (&mut band[..], &mut kept[..]);
            let mut r0 = 0usize;
            for g in 1..=groups {
                let want = total * g / groups;
                let mut r1 = r0;
                while r1 < nl && (count[r1 + 1] <= want || r1 == r0) {
                    r1 += 1;
                }
                if g == groups {
                    r1 = nl;
                }
                if r1 > r0 {
                    let (b, rb) = rest_b.split_at_mut(count[r1] - count[r0]);
                    let (k, rk) = rest_k.split_at_mut(r1 - r0);
                    parts.push((r0, r1, b, k));
                    rest_b = rb;
                    rest_k = rk;
                    r0 = r1;
                }
            }
            pool.install(|| parts.into_par_iter().for_each(|(r0, r1, b, k)| d.lines(r0, r1, b, k)));
        }
    }
    let mut m = 0usize;
    for row in 0..nl {
        for &(a, b) in &band[count[row]..count[row] + kept[row]] {
            row_out[m] = row as i64;
            lo_out[m] = a;
            hi_out[m] = b;
            m += 1;
        }
    }
    m as i64
}

/// band_rows' inputs, shared by the lines worked out side by side.
struct Band<'a> {
    ax: &'a [f64],
    ay: &'a [f64],
    bx: &'a [f64],
    by: &'a [f64],
    length: &'a [f64],
    ys: &'a [f64],
    r: f64,
    count: &'a [usize],
    ids: &'a [usize],
}

impl Band<'_> {
    /// Lines r0..r1: each line's bands into its room (band starts at line r0's), sorted, then joined in place
    /// (touching ones too); kept[line - r0] = how many joined stretches it has.
    #[inline(always)]
    fn lines(&self, r0: usize, r1: usize, band: &mut [(f64, f64)], kept: &mut [usize]) {
        let base = self.count[r0];
        for row in r0..r1 {
            let (s, t) = (self.count[row] - base, self.count[row + 1] - base);
            let y = self.ys[row];
            let mut k = s;
            for &e in &self.ids[s + base..t + base] {
                let (lo, hi) = band_at(self.ax[e], self.ay[e], self.bx[e], self.by[e], self.length[e], y, self.r);
                if hi > lo {
                    band[k] = (lo, hi);
                    k += 1;
                }
            }
            let part = &mut band[s..k];
            part.sort_unstable_by(|p, q| p.0.total_cmp(&q.0).then(p.1.total_cmp(&q.1)));
            let mut w = 0usize;
            let mut reach = f64::NEG_INFINITY;
            for i in 0..part.len() {
                let (a, b) = part[i];
                if a > reach {
                    part[w] = (a, b);
                    w += 1;
                    reach = b;
                } else if b > reach {
                    part[w - 1].1 = b;
                    reach = b;
                }
            }
            kept[row - r0] = w;
        }
    }
}

/// The edges (indices in near) that can be the nearest to some spot of the box x0..x1, y0..y1 -> how many kept
/// (in front). Leaving the others out changes no distance: each is further from every spot of the box than the
/// nearest edge to its middle can be (shrink._could_be_nearest, with room for rounding).
#[inline(always)]
#[allow(clippy::too_many_arguments)]
fn could_be_nearest(ax: &[f64], ay: &[f64], bx: &[f64], by: &[f64], near: &mut [usize], x0: f64, x1: f64,
                    y0: f64, y1: f64) -> usize {
    let k = near.len();
    if k < 64 {
        return k;
    }
    let (cx, cy) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0);
    let mut best = f64::INFINITY;
    for &e in near.iter() {
        let (dx, dy) = (bx[e] - ax[e], by[e] - ay[e]);
        let t = (((cx - ax[e]) * dx + (cy - ay[e]) * dy) / pmax(dx * dx + dy * dy, 1e-18)).clamp(0.0, 1.0);
        let (u, v) = (cx - ax[e] - t * dx, cy - ay[e] - t * dy);
        best = best.min(u * u + v * v);
    }
    let half = ((x1 - x0) * (x1 - x0) + (y1 - y0) * (y1 - y0)).sqrt() / 2.0;
    let most = (best.sqrt() + half) * (1.0 + 1e-6) + 1e-9;
    let mut kept = 0usize;
    for q in 0..k {
        let e = near[q];
        let gx = 0.0f64.max((ax[e].min(bx[e]) - x1).max(x0 - ax[e].max(bx[e])));
        let gy = 0.0f64.max((ay[e].min(by[e]) - y1).max(y0 - ay[e].max(by[e])));
        if gx * gx + gy * gy <= most * most {
            near[kept] = e;
            kept += 1;
        }
    }
    kept
}

/// fastloops.field_dist (dist: len(ys) rows of len(xs)) -> 0, FAILED = odd sizes.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
pub fn field_dist(ax: &[f64], ay: &[f64], bx: &[f64], by: &[f64], xs: &[f64], ys: &[f64], reach: f64, block: i64,
                  dist: NdMut<f64, 2>) -> i64 {
    let (ne, nx, ny) = (ax.len(), xs.len(), ys.len());
    if ay.len() != ne || bx.len() != ne || by.len() != ne || block < 1 || dist.sh[0] != ny || dist.sh[1] != nx
        || dist.d.len() != nx * ny {
        return FAILED;
    }
    let block = block as usize;
    if nx == 0 || ny == 0 {
        return 0;
    }
    let g = Grid { ax, ay, bx, by, xs, ys, reach, block };
    match crate::pool_for(true, nx * ny, 1) {
        None => {
            let mut near: Vec<usize> = need!(zeros(ne));
            for (j, d) in dist.d.chunks_mut(nx).enumerate() {
                g.row(j, d, &mut near);
            }
            0
        }
        Some(pool) => pool.install(|| {
            dist.d.par_chunks_mut(nx).enumerate().map(|(j, d)| match zeros::<usize>(ne) {
                Ok(mut near) => {
                    g.row(j, d, &mut near);
                    0
                }
                Err(code) => code,
            }).min().unwrap_or(0)
        }),
    }
}

/// field_dist's inputs, shared by the bands of blocks worked out side by side.
struct Grid<'a> {
    ax: &'a [f64],
    ay: &'a [f64],
    bx: &'a [f64],
    by: &'a [f64],
    xs: &'a [f64],
    ys: &'a [f64],
    reach: f64,
    block: usize,
}

impl Grid<'_> {
    /// Grid row j (d = that row), near = room for every edge. The edges looked at: those within reach of the
    /// row's whole block (as the NumPy code's blocks), then only those that can be nearest on this row.
    #[inline(always)]
    fn row(&self, j: usize, d: &mut [f64], near: &mut [usize]) {
        let (ax, ay, bx, by, xs, ys, reach, block) =
            (self.ax, self.ay, self.bx, self.by, self.xs, self.ys, self.reach, self.block);
        let (ne, nx, ny) = (ax.len(), xs.len(), ys.len());
        let j0 = j / block * block;
        let j1 = (j0 + block).min(ny);
        let py = ys[j];
        for i0 in (0..nx).step_by(block) {
            let i1 = (i0 + block).min(nx);
            let mut k = 0usize;
            for e in 0..ne {
                if pmax(ay[e], by[e]) >= ys[j0] - reach && pmin(ay[e], by[e]) <= ys[j1 - 1] + reach
                    && pmax(ax[e], bx[e]) >= xs[i0] - reach && pmin(ax[e], bx[e]) <= xs[i1 - 1] + reach {
                    near[k] = e;
                    k += 1;
                }
            }
            if k == 0 {
                continue;
            }
            let k = could_be_nearest(ax, ay, bx, by, &mut near[..k], xs[i0], xs[i1 - 1], py, py);
            let near = &near[..k];
            for i in i0..i1 {
                let px = xs[i];
                let mut best = f64::INFINITY;
                for &e in near {
                    let (dx, dy) = (bx[e] - ax[e], by[e] - ay[e]);
                    let mut t = ((px - ax[e]) * dx + (py - ay[e]) * dy) / pmax(dx * dx + dy * dy, 1e-18);
                    t = pmin(pmax(t, 0.0), 1.0);
                    let (u, v) = (px - ax[e] - t * dx, py - ay[e] - t * dy);
                    let d2 = u * u + v * v;
                    if d2 < best {
                        best = d2;
                    }
                }
                d[i] = pmin(best.sqrt(), 2.0 * reach);
            }
        }
    }
}

/// fastloops.blend_heat: Nested Blend's heat at spots (notes/blend_rows.py heat_at). Piece j (between nodes j and
/// j + 1, length dl[j]) of spot i holds row r[i] in a run of run_m modes (place run_p, first mode mode_at); its
/// value = sum over the run's modes of q_k (WS w_s + WE w_e) (+ the point source's part in piece j0). mu, wsc, wec,
/// mode_at, run_m, run_p: pieces x width, row by row. -> 0, FAILED = odd sizes.
#[allow(clippy::too_many_arguments)]
pub fn blend_heat(nodes: &[f64], dl: &[f64], mu: &[f64], wsc: &[f64], wec: &[f64], mode_at: &[i64], run_m: &[i64],
                  run_p: &[i64], width: i64, j: &[i64], r: &[i64], xs: &[f64], j0: i64, q: &[f64], cx: f64,
                  out: &mut [f64]) -> i64 {
    let (n, e) = (xs.len(), dl.len());
    if width < 1 || j.len() != n || r.len() != n || out.len() != n || nodes.len() != e + 1 {
        return FAILED;
    }
    let w = width as usize;
    let cells = e * w;
    if mu.len() != cells || wsc.len() != cells || wec.len() != cells || mode_at.len() != cells
        || run_m.len() != cells || run_p.len() != cells || q.len() != w {
        return FAILED;
    }
    for i in 0..n {
        if j[i] < 0 || j[i] as usize >= e || r[i] < 0 || r[i] as usize >= w {
            return FAILED;
        }
        let at = j[i] as usize * w + r[i] as usize;
        let (m, first) = (run_m[at], mode_at[at]);
        if m < 1 || first < 0 || (first + m) as usize > w {
            return FAILED;
        }
    }
    let h = Heat { nodes, dl, mu, wsc, wec, mode_at, run_m, run_p, w, j0, q, cx };
    match crate::pool_for(true, n, 4) {
        None => {
            for i in 0..n {
                out[i] = h.at(j[i] as usize, r[i] as usize, xs[i]);
            }
        }
        Some(pool) => pool.install(|| {
            out.par_iter_mut().enumerate().for_each(|(i, o)| *o = h.at(j[i] as usize, r[i] as usize, xs[i]));
        }),
    }
    0
}

/// blend_heat's inputs.
struct Heat<'a> {
    nodes: &'a [f64],
    dl: &'a [f64],
    mu: &'a [f64],
    wsc: &'a [f64],
    wec: &'a [f64],
    mode_at: &'a [i64],
    run_m: &'a [i64],
    run_p: &'a [i64],
    w: usize,
    j0: i64,
    q: &'a [f64],
    cx: f64,
}

impl Heat<'_> {
    /// The heat at x on row r of piece j (the twin's sums, one mode after another).
    fn at(&self, j: usize, r: usize, x: f64) -> f64 {
        let at = j * self.w + r;
        let (m, p, first) = (self.run_m[at], self.run_p[at] as f64, self.mode_at[at] as usize);
        let mf = m as f64;
        let (t, d) = (x - self.nodes[j], self.dl[j]);
        let (mut u, mut src) = (0.0, 0.0);
        let own = j as i64 == self.j0;
        for k in 0..m as usize {
            let mut qk = (2.0 / mf).sqrt() * (std::f64::consts::PI * ((p + 0.5) * (k + 1) as f64) / mf).sin();
            if k as i64 == m - 1 {
                qk *= 0.5f64.sqrt();
            }
            let c = j * self.w + first + k;
            let mu = self.mu[c];
            let em = -(-2.0 * mu * d).exp_m1();
            let ws = ((-mu * t).exp() - (-mu * (2.0 * d - t)).exp()) / em;
            let we = ((-mu * (d - t)).exp() - (-mu * (d + t)).exp()) / em;
            u += qk * (self.wsc[c] * ws + self.wec[c] * we);
            if own {
                src += qk * (self.q[first + k] * (-mu * (x - self.cx).abs()).exp() / (2.0 * mu));
            }
        }
        u + src
    }
}

/// Min-heap of (gap, edge) (fastloops._fh_push / _fh_pop: by gap, then edge).
#[inline(always)]
fn fh_less(k: &[f64], v: &[usize], i: usize, j: usize) -> bool {
    k[i] < k[j] || (k[i] == k[j] && v[i] < v[j])
}
#[inline(always)]
fn fh_push(k: &mut [f64], v: &mut [usize], n: usize, key: f64, val: usize) -> usize {
    let mut i = n;
    k[i] = key;
    v[i] = val;
    while i > 0 {
        let p = (i - 1) / 2;
        if !fh_less(k, v, i, p) {
            break;
        }
        k.swap(p, i);
        v.swap(p, i);
        i = p;
    }
    n + 1
}
#[inline(always)]
fn fh_pop(k: &mut [f64], v: &mut [usize], n: usize) -> usize {
    let n = n - 1;
    k.swap(0, n);
    v.swap(0, n);
    let mut i = 0;
    loop {
        let mut a = 2 * i + 1;
        if a >= n {
            break;
        }
        let b = a + 1;
        if b < n && fh_less(k, v, b, a) {
            a = b;
        }
        if !fh_less(k, v, a, i) {
            break;
        }
        k.swap(a, i);
        v.swap(a, i);
        i = a;
    }
    n
}

/// fastloops._box_gap: a lower bound of how far the box x0..x1, y0..y1 is from the stretches lo / hi of the line
/// at y.
#[inline(always)]
fn box_gap(x0: f64, x1: f64, y0: f64, y1: f64, y: f64, lo: &[f64], hi: &[f64]) -> f64 {
    let gy = pmax(0.0, pmax(y0 - y, y - y1));
    let (mut a, mut b) = (0usize, lo.len());
    while a < b {
        let mid = (a + b) / 2;
        if lo[mid] <= x1 {
            a = mid + 1;
        } else {
            b = mid;
        }
    }
    let gx = if a >= 1 && hi[a - 1] > x0 {
        0.0
    } else {
        let mut gx = f64::INFINITY;
        if a >= 1 {
            gx = x0 - hi[a - 1];
        }
        if a < lo.len() {
            gx = pmin(gx, lo[a] - x1);
        }
        gx
    };
    (gx * gx + gy * gy).sqrt()
}

/// band_all's inputs, shared by the keys worked out side by side.
struct BandAll<'a> {
    ax: &'a [f64],
    ay: &'a [f64],
    bx: &'a [f64],
    by: &'a [f64],
    length: &'a [f64],
    ex0: &'a [f64],
    ex1: &'a [f64],
    ey0: &'a [f64],
    ey1: &'a [f64],
    ys: &'a [f64],
    shift: &'a [f64],
    lkey: &'a [i64],
    in_off: &'a [i64],
    in_lo: &'a [f64],
    in_hi: &'a [f64],
    rs: &'a [f64],
    ppq: f64,
    per: usize,
    rmax: f64,
}

impl BandAll<'_> {
    /// fastloops._band_key for keys q0..q1: their rows onto out (start, end, key, step).
    fn keys(&self, q0: usize, q1: usize, out: &mut [Vec<i64>; 4]) -> Result<(), i64> {
        let (ne, per) = (self.ax.len(), self.per);
        let mut klo: Vec<Vec<f64>> = room(per)?;
        let mut khi: Vec<Vec<f64>> = room(per)?;
        let mut hk: Vec<Vec<f64>> = room(per)?;
        let mut hv: Vec<Vec<usize>> = room(per)?;
        for _ in 0..per {
            klo.push(Vec::new());
            khi.push(Vec::new());
            hk.push(zeros(ne + 1)?);
            hv.push(zeros(ne + 1)?);
        }
        let mut hn: Vec<usize> = zeros(per)?;
        let (mut act, mut act_l): (Vec<usize>, Vec<f64>) = (zeros(ne + 1)?, zeros(ne + 1)?);
        let mut band: Vec<(f64, f64)> = filled(ne + 1, (0.0, 0.0))?;
        let (mut nlo, mut nhi): (Vec<f64>, Vec<f64>) = (zeros(ne + 1)?, zeros(ne + 1)?);
        let (mut wlo, mut whi): (Vec<f64>, Vec<f64>) = (Vec::new(), Vec::new());
        let mut tmp: Vec<(f64, f64)> = Vec::new();
        for q in q0..q1 {
            for j in 0..per {
                let g = q * per + j;
                let (s, t) = (self.in_off[g] as usize, self.in_off[g + 1] as usize);
                klo[j].clear();
                khi[j].clear();
                for x in s..t {
                    grow(&mut klo[j], self.in_lo[x])?;
                    grow(&mut khi[j], self.in_hi[x])?;
                }
                hn[j] = 0;
                if t > s {
                    let (lo, hi) = (&self.in_lo[s..t], &self.in_hi[s..t]);
                    for e in 0..ne {
                        let gap = box_gap(self.ex0[e], self.ex1[e], self.ey0[e], self.ey1[e], self.ys[g], lo, hi);
                        if gap < self.rmax {
                            hn[j] = fh_push(&mut hk[j], &mut hv[j], hn[j], gap, e);
                        }
                    }
                }
            }
            for (i, &r) in self.rs.iter().enumerate() {
                let rm = r * (1.0 + 1e-9) + 1e-9;
                tmp.clear();
                for j in 0..per {
                    if klo[j].is_empty() {
                        continue;
                    }
                    let g = q * per + j;
                    let y = self.ys[g];
                    let mut na = 0usize;
                    while hn[j] > 0 && hk[j][0] < rm {
                        hn[j] = fh_pop(&mut hk[j], &mut hv[j], hn[j]);
                        let e = hv[j][hn[j]];
                        let gap = box_gap(self.ex0[e], self.ex1[e], self.ey0[e], self.ey1[e], y, &klo[j], &khi[j]);
                        if gap < rm {
                            act[na] = e;
                            act_l[na] = gap;
                            na += 1;
                        } else {
                            hn[j] = fh_push(&mut hk[j], &mut hv[j], hn[j], gap, e);
                        }
                    }
                    let mut nb = 0usize;
                    for x in 0..na {
                        let e = act[x];
                        let (lo, hi) = band_at(self.ax[e], self.ay[e], self.bx[e], self.by[e], self.length[e], y, r);
                        if hi > lo {
                            band[nb] = (lo, hi);
                            nb += 1;
                        }
                        hn[j] = fh_push(&mut hk[j], &mut hv[j], hn[j], act_l[x], e);
                    }
                    let part = &mut band[..nb];
                    part.sort_unstable_by(|p, q| p.0.total_cmp(&q.0).then(p.1.total_cmp(&q.1)));
                    let mut nn = 0usize;
                    let mut reach = f64::NEG_INFINITY;
                    for &(a, b) in part.iter() {
                        if a > reach {
                            nlo[nn] = a;
                            nhi[nn] = b;
                            nn += 1;
                            reach = b;
                        } else if b > reach {
                            nhi[nn - 1] = b;
                            reach = b;
                        }
                    }
                    wlo.clear(); // (shrink.minus: what's kept with the bands taken out)
                    whi.clear();
                    let mut k = 0usize;
                    for x in 0..klo[j].len() {
                        let (mut a, b) = (klo[j][x], khi[j][x]);
                        while k < nn && nhi[k] <= a {
                            k += 1;
                        }
                        for z in k..nn {
                            let (c, d) = (nlo[z], nhi[z]);
                            if c >= b {
                                break;
                            }
                            if d <= a {
                                continue;
                            }
                            if c > a {
                                grow(&mut wlo, a)?;
                                grow(&mut whi, c)?;
                            }
                            a = pmax(a, d);
                            if a >= b {
                                break;
                            }
                        }
                        if a < b {
                            grow(&mut wlo, a)?;
                            grow(&mut whi, b)?;
                        }
                    }
                    std::mem::swap(&mut klo[j], &mut wlo);
                    std::mem::swap(&mut khi[j], &mut whi);
                    let sh = self.shift[g];
                    for x in 0..klo[j].len() {
                        grow(&mut tmp, (klo[j][x] - sh, khi[j][x] - sh))?;
                    }
                }
                let mut made = 0usize;
                if !tmp.is_empty() {
                    tmp.sort_unstable_by(|p, q| p.0.total_cmp(&q.0));
                    let (mut ca, mut cb) = tmp[0];
                    let nt = tmp.len();
                    for s in 1..=nt {
                        if s < nt && tmp[s].0 <= cb {
                            cb = pmax(cb, tmp[s].1);
                            continue;
                        }
                        let a = (ca * self.ppq + 0.5).floor() as i64;
                        let b = (cb * self.ppq + 0.5).floor() as i64;
                        if b > a {
                            for (o, v) in out.iter_mut().zip([a, b, self.lkey[q * per], i as i64]) {
                                grow(o, v)?;
                            }
                            made += 1;
                        }
                        if s < nt {
                            (ca, cb) = tmp[s];
                        }
                    }
                }
                if made == 0 {
                    break; // (a key with no notes is left out of the steps after it)
                }
            }
        }
        Ok(())
    }
}

/// fastloops.band_all (nested.band_steps' deeper steps, all at once) -> (start, end, key, step) rows, key by key.
pub fn band_all(ax: &[f64], ay: &[f64], bx: &[f64], by: &[f64], length: &[f64], ys: &[f64], shift: &[f64],
                lkey: &[i64], in_off: &[i64], in_lo: &[f64], in_hi: &[f64], rs: &[f64], ppq: i64, per: i64)
                -> Outs {
    let (ne, nl) = (ax.len(), ys.len());
    if ay.len() != ne || bx.len() != ne || by.len() != ne || length.len() != ne || shift.len() != nl
        || lkey.len() != nl || in_off.len() != nl + 1 || per <= 0 || nl % per as usize != 0 {
        return Err(FAILED);
    }
    for g in 0..nl {
        let (a, b) = (in_off[g], in_off[g + 1]);
        if a < 0 || b < a || b as usize > in_lo.len() || b as usize > in_hi.len() {
            return Err(FAILED);
        }
    }
    let empty = || Ok(vec![Out::I64(Vec::new(), 0), Out::I64(Vec::new(), 0), Out::I64(Vec::new(), 0),
                           Out::I64(Vec::new(), 0)]);
    if rs.is_empty() || nl == 0 {
        return empty();
    }
    let (mut ex0, mut ex1, mut ey0, mut ey1): (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) =
        (zeros(ne)?, zeros(ne)?, zeros(ne)?, zeros(ne)?);
    for e in 0..ne {
        ex0[e] = pmin(ax[e], bx[e]);
        ex1[e] = pmax(ax[e], bx[e]);
        ey0[e] = pmin(ay[e], by[e]);
        ey1[e] = pmax(ay[e], by[e]);
    }
    let per = per as usize;
    let nq = nl / per;
    let d = BandAll {
        ax, ay, bx, by, length, ex0: &ex0, ex1: &ex1, ey0: &ey0, ey1: &ey1, ys, shift, lkey, in_off, in_lo, in_hi, rs,
        ppq: ppq as f64, per, rmax: rs[rs.len() - 1] * (1.0 + 1e-9) + 1e-9,
    };
    let parts: Vec<[Vec<i64>; 4]> = match crate::pool_for(true, nq.saturating_mul(ne).saturating_mul(rs.len()), 1) {
        None => {
            let mut out: [Vec<i64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
            d.keys(0, nq, &mut out)?;
            vec![out]
        }
        Some(pool) => {
            let groups = (pool.current_num_threads() * 8).min(nq).max(1);
            let cuts: Vec<(usize, usize)> = (0..groups).map(|g| (nq * g / groups, nq * (g + 1) / groups)).collect();
            let got: Vec<Result<[Vec<i64>; 4], i64>> = pool.install(|| {
                cuts.par_iter().map(|&(a, b)| {
                    let mut out: [Vec<i64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
                    d.keys(a, b, &mut out).map(|_| out)
                }).collect()
            });
            let mut parts = room(got.len())?;
            for p in got {
                parts.push(p?);
            }
            parts
        }
    };
    let total: usize = parts.iter().map(|p| p[0].len()).sum();
    let mut out: [Vec<i64>; 4] = [room(total)?, room(total)?, room(total)?, room(total)?];
    for p in parts {
        for (o, q) in out.iter_mut().zip(p.iter()) {
            o.extend_from_slice(q);
        }
    }
    let [a, b, c, k] = out;
    Ok(vec![Out::I64(a, 0), Out::I64(b, 0), Out::I64(c, 0), Out::I64(k, 0)])
}

/// fastloops._short_emit: row (a, b, k, t) onto out, joined to step t's last row when it touches it.
#[inline(always)]
fn short_emit(out: &mut [Vec<i64>; 4], last: &mut [i64], a: i64, b: i64, k: i64, t: usize) -> Result<(), i64> {
    let j = last[t];
    if j >= 0 && out[2][j as usize] == k && out[1][j as usize] == a {
        out[1][j as usize] = b;
        return Ok(());
    }
    last[t] = out[0].len() as i64;
    for (o, v) in out.iter_mut().zip([a, b, k, t as i64]) {
        grow(o, v)?;
    }
    Ok(())
}

/// Min-heap of (first, second) pairs (fastloops._pair_push / _pair_pop: the same order).
#[inline(always)]
fn pair_less(h: &[[i64; 2]], i: usize, j: usize) -> bool {
    h[i][0] < h[j][0] || (h[i][0] == h[j][0] && h[i][1] < h[j][1])
}
#[inline(always)]
fn pair_push(h: &mut [[i64; 2]], n: usize, x: [i64; 2]) -> usize {
    let mut i = n;
    h[i] = x;
    while i > 0 {
        let p = (i - 1) / 2;
        if !pair_less(h, i, p) {
            break;
        }
        h.swap(p, i);
        i = p;
    }
    n + 1
}
#[inline(always)]
fn pair_pop(h: &mut [[i64; 2]], n: usize) -> usize {
    let n = n - 1;
    h.swap(0, n);
    let mut i = 0;
    loop {
        let mut a = 2 * i + 1;
        if a >= n {
            break;
        }
        let b = a + 1;
        if b < n && pair_less(h, b, a) {
            a = b;
        }
        if !pair_less(h, a, i) {
            break;
        }
        h.swap(a, i);
        i = a;
    }
    n
}

/// fastloops.short_pieces (nested.shortest_pieces): the inside's stretches (bs, be, bk) and the later steps' (rs,
/// re, rk, step rd >= 1), both sorted by key, then start -> (a, b, key, t) rows (see there).
pub fn short_pieces(bs: &[i64], be: &[i64], bk: &[i64], rs: &[i64], re: &[i64], rk: &[i64], rd: &[i64],
                    least: i64) -> Outs {
    let (nb, m) = (bs.len(), rs.len());
    if be.len() < nb || bk.len() < nb || re.len() < m || rk.len() < m || rd.len() < m {
        return Err(FAILED);
    }
    let mut top: Vec<i64> = zeros(m)?;
    let mut most = 0i64;
    for j in 0..m {
        top[j] = if j == 0 || rk[j] != rk[j - 1] { re[j] } else { top[j - 1].max(re[j]) };
        most = most.max(rd[j]);
    }
    if most < 0 {
        return Err(FAILED);
    }
    let most = most as usize;
    let mut last: Vec<i64> = filled(most + 1, -1)?;
    let mut opened: Vec<i64> = zeros(most + 1)?;
    let (mut ca, mut cb, mut cd): (Vec<i64>, Vec<i64>, Vec<i64>) = (zeros(m)?, zeros(m)?, zeros(m)?);
    let mut cuts: Vec<i64> = zeros(2 * m + 2)?;
    let mut heap: Vec<[i64; 2]> = filled(4 * m + 4, [0, 0])?;
    let p = 2 * m + 1;
    let (mut ps, mut pe, mut pd): (Vec<i64>, Vec<i64>, Vec<i64>) = (zeros(p)?, zeros(p)?, zeros(p)?);
    let (mut prv, mut nxt): (Vec<i64>, Vec<i64>) = (zeros(p)?, zeros(p)?);
    let mut alive: Vec<bool> = zeros(p)?;
    let mut out: [Vec<i64>; 4] = [Vec::new(), Vec::new(), Vec::new(), Vec::new()];
    for i in 0..nb {
        let (s0, e0, k) = (bs[i], be[i], bk[i]);
        let (mut lo, mut hi) = (0usize, m); // (the first stretch at or past (k, e0))
        while lo < hi {
            let mid = (lo + hi) / 2;
            if rk[mid] < k || (rk[mid] == k && rs[mid] < e0) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        let mut c = 0usize;
        let mut j = lo;
        while j > 0 && rk[j - 1] == k && top[j - 1] > s0 {
            j -= 1;
            if re[j] > s0 {
                let (a, b) = (rs[j].max(s0), re[j].min(e0));
                if b > a {
                    ca[c] = a;
                    cb[c] = b;
                    cd[c] = rd[j];
                    c += 1;
                }
            }
        }
        if c == 0 {
            continue;
        }
        ca[..c].reverse(); // (start order)
        cb[..c].reverse();
        cd[..c].reverse();
        cuts[0] = s0;
        cuts[1] = e0;
        for x in 0..c {
            cuts[2 + 2 * x] = ca[x];
            cuts[3 + 2 * x] = cb[x];
        }
        let cu = &mut cuts[..2 * c + 2];
        cu.sort_unstable();
        let mut nc = 0usize;
        for x in 0..cu.len() {
            if nc == 0 || cu[x] != cu[nc - 1] {
                cu[nc] = cu[x];
                nc += 1;
            }
        }
        let (mut n, mut nh, mut q) = (0usize, 0usize, 0usize);
        for x in 0..nc - 1 {
            let at = cu[x];
            while q < c && ca[q] <= at {
                nh = pair_push(&mut heap, nh, [-cd[q], cb[q]]);
                q += 1;
            }
            while nh > 0 && heap[0][1] <= at {
                nh = pair_pop(&mut heap, nh);
            }
            let d = if nh > 0 { -heap[0][0] } else { 0 };
            if n > 0 && pd[n - 1] == d {
                pe[n - 1] = cu[x + 1];
            } else {
                ps[n] = at;
                pe[n] = cu[x + 1];
                pd[n] = d;
                n += 1;
            }
        }
        nh = 0;
        for x in 0..n {
            prv[x] = x as i64 - 1;
            nxt[x] = if x + 1 < n { x as i64 + 1 } else { -1 };
            alive[x] = true;
            if pe[x] - ps[x] < least {
                nh = pair_push(&mut heap, nh, [pe[x] - ps[x], x as i64]);
            }
        }
        let mut left_n = n;
        while left_n > 1 && nh > 0 {
            nh = pair_pop(&mut heap, nh);
            let [ln, xi] = heap[nh];
            let x = xi as usize;
            if !alive[x] || pe[x] - ps[x] != ln {
                continue;
            }
            let (le, ri) = (prv[x], nxt[x]);
            let take = if le >= 0 && ri >= 0 && pd[le as usize] != pd[ri as usize] {
                let (l, r) = (le as usize, ri as usize);
                if pe[l] - ps[l] >= pe[r] - ps[r] { l } else { r }
            } else if le >= 0 {
                le as usize
            } else {
                ri as usize
            };
            pd[x] = pd[take];
            let mut node = x;
            if le >= 0 && pd[le as usize] == pd[x] {
                let l = le as usize;
                pe[l] = pe[x];
                nxt[l] = nxt[x];
                if nxt[x] >= 0 {
                    prv[nxt[x] as usize] = le;
                }
                alive[x] = false;
                left_n -= 1;
                node = l;
            }
            if ri >= 0 && pd[ri as usize] == pd[node] {
                let r = ri as usize;
                pe[node] = pe[r];
                nxt[node] = nxt[r];
                if nxt[r] >= 0 {
                    prv[nxt[r] as usize] = node as i64;
                }
                alive[r] = false;
                left_n -= 1;
            }
            if pe[node] - ps[node] < least {
                nh = pair_push(&mut heap, nh, [pe[node] - ps[node], node as i64]);
            }
        }
        let mut was = 0usize;
        let mut x = 0i64;
        while x >= 0 {
            let xu = x as usize;
            let d = pd[xu].max(0) as usize;
            for t in was + 1..=d {
                opened[t] = ps[xu];
            }
            for t in d + 1..=was {
                short_emit(&mut out, &mut last, opened[t], ps[xu], k, t)?;
            }
            was = d;
            x = nxt[xu];
        }
        for t in 1..=was {
            short_emit(&mut out, &mut last, opened[t], e0, k, t)?;
        }
    }
    let [a, b, c, d] = out;
    Ok(vec![Out::I64(a, 0), Out::I64(b, 0), Out::I64(c, 0), Out::I64(d, 0)])
}
