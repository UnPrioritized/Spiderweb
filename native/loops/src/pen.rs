//! The freehand pen's varying band (notes/pen.py band_rows: taper, flat ends, width from speed): every piece's
//! band (the hull of its two circles, cut square at a flat end) across every key line it reaches, joined line by
//! line. The same sums in the same order as pen.py's NumPy way (_pen_rows_np), which is its twin.
use crate::maths::{pmax, pmin};
use rayon::prelude::*;

use crate::{filled, need, zeros, Nd, FAILED};

/// Columns of a piece's row in `edges` (pen.py _pen_edges): its two ends, their half widths, has = its two
/// tangent lines exist (1.0), then tangent 1 (P -> Q) and tangent 2.
const EW: usize = 15;
/// Columns of a piece's row in `clips`: start cut (on, ox, oy, vx, vy), end cut (the same).
const CW: usize = 10;

/// One piece's band on the line at y -> (lo, hi), lo >= hi = none.
#[inline(always)]
fn piece_at(p: &[f64], c: &[f64], y: f64) -> (f64, f64) {
    let (ax, ay, bx, by, ra, rb, has) = (p[0], p[1], p[2], p[3], p[4], p[5], p[6]);
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    // (a line the band only TOUCHES gets nothing, like band_rows' plain pen: circles strictly crossed, a tangent
    // lying on the line left out)
    let h = ra * ra - (y - ay) * (y - ay);
    if h > 0.0 {
        let w = h.sqrt();
        lo = pmin(lo, ax - w);
        hi = pmax(hi, ax + w);
    }
    let h = rb * rb - (y - by) * (y - by);
    if h > 0.0 {
        let w = h.sqrt();
        lo = pmin(lo, bx - w);
        hi = pmax(hi, bx + w);
    }
    if has != 0.0 {
        for t in [7usize, 11] {
            let (px, py, qx, qy) = (p[t], p[t + 1], p[t + 2], p[t + 3]);
            let span = qy - py;
            if (py - y) * (qy - y) <= 0.0 && span.abs() > 1e-12 {
                let x = px + (y - py) / span * (qx - px);
                lo = pmin(lo, x);
                hi = pmax(hi, x);
            }
        }
    }
    for k in [0usize, 5] {
        if c[k] != 0.0 {
            let (ox, oy, vx, vy) = (c[k + 1], c[k + 2], c[k + 3], c[k + 4]);
            if vx.abs() < 1e-12 {
                if (y - oy) * vy < 0.0 {
                    lo = f64::INFINITY;
                    hi = f64::NEG_INFINITY;
                }
            } else {
                let edge = ox - (y - oy) * vy / vx;
                if vx > 0.0 {
                    lo = pmax(lo, edge);
                } else {
                    hi = pmin(hi, edge);
                }
            }
        }
    }
    (lo, hi)
}

/// pen.py band_rows' loop: pieces (edges: n x EW, clips: n x CW) reaching lines first[e] .. first[e] + n[e] - 1 of
/// ys -> how many joined stretches, line by line in row_out / lo_out / hi_out (room: n.sum()); FAILED = odd sizes.
#[inline(always)]
#[allow(clippy::too_many_arguments)]
pub fn pen_rows(edges: Nd<f64, 2>, clips: Nd<f64, 2>, ys: &[f64], first: &[i64], n: &[i64], row_out: &mut [i64],
                lo_out: &mut [f64], hi_out: &mut [f64]) -> i64 {
    let (ne, nl) = (edges.sh[0], ys.len());
    if edges.sh[1] != EW || clips.sh[0] != ne || clips.sh[1] != CW || edges.d.len() < ne * EW
        || clips.d.len() < ne * CW || first.len() != ne || n.len() != ne {
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
    let mut count: Vec<usize> = need!(zeros(nl + 1));
    for e in 0..ne {
        for row in first[e] as usize..(first[e] + n[e]) as usize {
            count[row + 1] += 1;
        }
    }
    for i in 0..nl {
        count[i + 1] += count[i];
    }
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
    let d = Pen { edges: edges.d, clips: clips.d, ys, count: &count, ids: &ids };
    match crate::pool_for(true, total, 4) {
        None => d.lines(0, nl, &mut band, &mut kept),
        Some(pool) => {
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
            pool.install(|| parts.into_par_iter().for_each(|(r0, r1, b, k)| pen_lines(&d, r0, r1, b, k)));
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

/// pen_rows' inputs, shared by the lines worked out side by side.
struct Pen<'a> {
    edges: &'a [f64],
    clips: &'a [f64],
    ys: &'a [f64],
    count: &'a [usize],
    ids: &'a [usize],
}

impl Pen<'_> {
    /// Lines r0..r1 (band starts at line r0's room): each piece's band, sorted, joined (touching ones too).
    #[inline(always)]
    fn lines(&self, r0: usize, r1: usize, band: &mut [(f64, f64)], kept: &mut [usize]) {
        let base = self.count[r0];
        for row in r0..r1 {
            let (s, t) = (self.count[row] - base, self.count[row + 1] - base);
            let y = self.ys[row];
            let mut k = s;
            for &e in &self.ids[s + base..t + base] {
                let (lo, hi) = piece_at(&self.edges[e * EW..e * EW + EW], &self.clips[e * CW..e * CW + CW], y);
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

#[inline(always)]
fn pen_lines_inner(d: &Pen<'_>, r0: usize, r1: usize, b: &mut [(f64, f64)], k: &mut [usize]) {
    d.lines(r0, r1, b, k)
}
level_builds!(fn pen_lines(d: &Pen<'_>, r0: usize, r1: usize, b: &mut [(f64, f64)], k: &mut [usize]) => pen_lines_inner);
