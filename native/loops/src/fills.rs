//! Inside fills' distances to the outline (notes/shrink.py: Even band, Outline gate, Nested's field): fastloops'
//! band_rows, field_dist. The same sums in the same order as those.
use crate::maths::{pmax, pmin};
use rayon::prelude::*;

use crate::{filled, need, zeros, NdMut, FAILED};

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
