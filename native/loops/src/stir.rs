//! Stir (recolour.stir_from's middle), note by note: fastloops.stir_spots' twin.
use std::f64::consts::PI;

use rayon::prelude::*;

use crate::maths::{npmod, pmax, pmin, rint};

/// The stir's numbers (recolour.stir_numbers): floats, whole numbers (fixed sizes: no index checks inside).
type FP = [f64; 32];
type IP = [i64; 23];

/// recolour.area_spot_q of one spot (a circle / square / diamond area): 0 in its middle, 1 on its outline and past
/// it, rounded to 9 places.
#[inline(always)]
fn stir_q(x: f64, y: f64, fp: &FP, ip: &IP) -> f64 {
    let mut xx = (x - fp[9]) * fp[11] / fp[13];
    let mut yy = (y - fp[10]) * fp[12] / fp[13];
    if ip[3] != 0 {
        let (nx, ny) = (xx * fp[14] + yy * fp[15], yy * fp[14] - xx * fp[15]);
        xx = nx;
        yy = ny;
    }
    if ip[4] != 0 {
        xx = xx - fp[16] * yy;
    }
    xx = xx / fp[17];
    yy = yy / fp[18];
    let d = if ip[2] == 1 {
        pmax(xx.abs(), yy.abs())
    } else if ip[2] == 2 {
        xx.abs() + yy.abs()
    } else {
        xx.hypot(yy)
    };
    let d = rint(d * 1e9) / 1e9;
    pmin(pmax(d, 0.0), 1.0)
}

/// One spot: (x, y) in the colour box -> where the stir looks (before from_unit): Bulge, the more bends, Turn /
/// Twist (+ the Area's fade), the edges' clip / tile, Squeeze.
#[inline(always)]
fn stir_one(mut px: f64, mut py: f64, fp: &FP, ip: &IP) -> (f64, f64) {
    let (cx, cy, rx, ry) = (fp[0], fp[1], fp[2], fp[3]);
    let tau = 2.0 * PI;
    if ip[9] != 0 {
        // Bulge
        let (mx, my, q);
        if ip[0] != 0 && ip[8] != 0 {
            mx = fp[9];
            my = fp[10];
            q = stir_q(px, py, fp, ip);
        } else {
            mx = cx;
            my = cy;
            let (dx, dy) = (px - mx, py - my);
            let a = if dx > 0.0 { dx / pmax(1.0 - mx, 1e-12) } else { -dx / pmax(mx, 1e-12) };
            let b = if dy > 0.0 { dy / pmax(1.0 - my, 1e-12) } else { -dy / pmax(my, 1e-12) };
            q = a.hypot(b);
        }
        let mut g = 1.0;
        if q < 1.0 && q > 1e-12 {
            g = 1.0 / (fp[19] - fp[20] * q);
        }
        px = mx + (px - mx) * g;
        py = my + (py - my) * g;
    }
    let mut w = 1.0;
    let mut q = 0.0;
    let mut hard = 0.0;
    if ip[0] != 0 {
        // (the Area's fade, measured where the note is seen)
        q = stir_q(px, py, fp, ip);
        hard = if q < 1.0 { 1.0 } else { 0.0 };
        w = if ip[1] != 0 { hard } else { 1.0 - q * q * (3.0 - 2.0 * q) };
    }
    if ip[10] != 0 {
        // Kaleidoscope
        let inside = ip[0] != 0 && ip[14] != 0;
        let (mx, my) = if inside { (fp[9], fp[10]) } else { (cx, cy) };
        let (xx, yy) = ((px - mx) * rx, (py - my) * ry);
        let mut a = npmod(yy.atan2(xx) - fp[21], fp[22]);
        if a > fp[23] {
            a = fp[22] - a;
        }
        a = a + fp[21];
        let r = xx.hypot(yy);
        let (nx, ny) = (mx + r * a.cos() / rx, my + r * a.sin() / ry);
        let f = if inside { (if ip[18] != 0 { hard } else { w }) * 1.0 } else { 1.0 };
        px = px + (nx - px) * f;
        py = py + (ny - py) * f;
    }
    if ip[11] >= 0 {
        // Ripple
        let inside = ip[0] != 0 && ip[15] != 0;
        let (mx, my) = if inside { (fp[9], fp[10]) } else { (cx, cy) };
        let (mut xx, mut yy) = ((px - mx) * rx, (py - my) * ry);
        if ip[11] == 0 {
            xx = xx + fp[24] * (tau * yy / fp[25]).sin();
        } else if ip[11] == 1 {
            yy = yy + fp[24] * (tau * xx / fp[25]).sin();
        } else {
            let r = xx.hypot(yy);
            let g = if r > 1e-12 { (r + fp[24] * (tau * r / fp[25]).sin()) / pmax(r, 1e-12) } else { 1.0 };
            xx = xx * g;
            yy = yy * g;
        }
        let (nx, ny) = (mx + xx / rx, my + yy / ry);
        let f = if inside { w * 1.0 } else { 1.0 };
        px = px + (nx - px) * f;
        py = py + (ny - py) * f;
    }
    if ip[12] != 0 {
        // Twist along a line
        let inside = ip[0] != 0 && ip[16] != 0;
        let (mx, my) = if inside { (fp[9], fp[10]) } else { (cx, cy) };
        let (xx, yy) = ((px - mx) * rx, (py - my) * ry);
        let (mut u, mut v) = (xx * fp[26] + yy * fp[27], yy * fp[26] - xx * fp[27]);
        let phi = fp[28] * u / (if inside { fp[31] } else { fp[30] });
        let (nu, nv) = (u + v * phi.sin(), v * phi.cos());
        u = nu;
        v = nv;
        let (nx, ny) = (mx + (u * fp[26] - v * fp[27]) / rx, my + (u * fp[27] + v * fp[26]) / ry);
        let f = if inside { w * 1.0 } else { 1.0 };
        px = px + (nx - px) * f;
        py = py + (ny - py) * f;
    }
    if ip[13] != 0 {
        // Straight to round
        let inside = ip[0] != 0 && ip[17] != 0;
        let (mx, my) = if inside { (fp[9], fp[10]) } else { (cx, cy) };
        let (xx, yy) = ((px - mx) * rx, (py - my) * ry);
        let across = (yy.atan2(xx) + PI) / tau;
        let up = if inside { stir_q(px, py, fp, ip) } else { xx.hypot(yy) / fp[30] };
        let f = fp[29] * (if inside { if ip[18] != 0 { hard } else { w } } else { 1.0 });
        let npx = px + (across - px) * f;
        let npy = py + (up - py) * f;
        px = npx;
        py = npy;
    }
    let (dx, dy) = (px - cx, py - cy);
    let (ca, sa);
    if ip[20] != 0 {
        // (a turn of its own for every note)
        let mut turn = fp[4];
        if ip[0] != 0 {
            if ip[5] != 0 {
                turn = turn * w;
            }
            if fp[5] != 0.0 && ip[6] != 0 {
                turn = turn + fp[5] * q.sqrt() * w;
            }
        }
        if ip[19] != 0 {
            turn = turn + fp[5] * ((dx * rx).hypot(dy * ry) / pmax(fp[8], 1e-12)).sqrt();
        }
        let a = -2.0 * PI * turn;
        ca = a.cos();
        sa = a.sin();
    } else {
        ca = fp[6];
        sa = fp[7];
    }
    let (xx, yy) = (dx * rx, dy * ry);
    px = cx + (xx * ca - yy * sa) / rx;
    py = cy + (xx * sa + yy * ca) / ry;
    if ip[21] != 0 {
        // Escape
        px = pmin(pmax(px, 0.0), 1.0);
        py = pmin(pmax(py, 0.0), 1.0);
    } else {
        if px < -1e-9 || px > 1.0 + 1e-9 {
            px = npmod(px, 1.0);
        }
        if py < -1e-9 || py > 1.0 + 1e-9 {
            py = npmod(py, 1.0);
        }
        px = pmin(pmax(px, 0.0), 1.0);
        py = pmin(pmax(py, 0.0), 1.0);
    }
    if ip[22] != 0 {
        // Squeeze
        let mut sx = if px < cx { 0.5 * px / pmax(cx, 1e-12) } else { 0.5 + 0.5 * (px - cx) / pmax(1.0 - cx, 1e-12) };
        let mut sy = if py < cy { 0.5 * py / pmax(cy, 1e-12) } else { 0.5 + 0.5 * (py - cy) / pmax(1.0 - cy, 1e-12) };
        if ip[0] != 0 && ip[7] != 0 {
            sx = px + (sx - px) * w;
            sy = py + (sy - py) * w;
        }
        px = sx;
        py = sy;
    }
    (px, py)
}

#[inline(always)]
fn range_inner(x: &[f64], y: &[f64], fp: &FP, ip: &IP, ox: &mut [f64], oy: &mut [f64]) {
    for (((&x, &y), ox), oy) in x.iter().zip(y).zip(ox.iter_mut()).zip(oy.iter_mut()) {
        (*ox, *oy) = stir_one(x, y, fp, ip);
    }
}

level_builds!(fn range(x: &[f64], y: &[f64], fp: &FP, ip: &IP, ox: &mut [f64], oy: &mut [f64]) => range_inner);

/// Every spot; many = shared out over the pool's threads (each spot on its own: the very same numbers). -> 0.
pub fn spots(x: &[f64], y: &[f64], fp: &[f64], ip: &[i64], ox: &mut [f64], oy: &mut [f64], many: bool) -> i64 {
    let n = x.len();
    // (too few numbers or outputs = a panic here, caught: never a wrong read)
    let (fp, ip): (&FP, &IP) = (fp[..32].try_into().unwrap(), ip[..23].try_into().unwrap());
    let (y, ox, oy) = (&y[..n], &mut ox[..n], &mut oy[..n]);
    match crate::pool_for(many, n, 1) {
        None => range(x, y, fp, ip, ox, oy),
        Some(pool) => {
            let chunk = (n / (pool.current_num_threads() * 8)).max(1024);
            pool.install(|| {
                (x.par_chunks(chunk), y.par_chunks(chunk), ox.par_chunks_mut(chunk), oy.par_chunks_mut(chunk))
                    .into_par_iter()
                    .for_each(|(xc, yc, oxc, oyc)| range(xc, yc, fp, ip, oxc, oyc));
            });
        }
    }
    0
}
