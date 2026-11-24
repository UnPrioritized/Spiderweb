//! Custom shape outlines -> notes (custom.paths_outline, 2026-11-24): every joined path made like a line in one call,
//! instead of many small NumPy steps a stroke. The very same notes in the same order as the NumPy way (paths.dedupe,
//! loop_from_left, stretch_ends, line_notes, parts_notes: the same sums in the same order), which is its twin (not in
//! NEEDS: no plain twin); keep_longest stays NumPy. Odd input (NaN, huge numbers, an empty path) = FAILED: the NumPy
//! way does that call.

use crate::maths::rint;
use crate::pyext::{Out, Outs};
use crate::{grow, FAILED, NO_MEMORY};

const EDGE: f64 = 0.5 - 1e-6; // (paths.EDGE)
const ROW_EDGE: f64 = 1e-7; // (paths.ROW_EDGE)
const BIG: f64 = 1e15; // (ticks past this: FAILED, whole numbers stay exact)
const KEYS_BIG: f64 = 1e7; // (key heights past this: FAILED, the NumPy way runs out of memory the same)

/// v emptied with room for n, or NO_MEMORY.
#[inline(always)]
fn fit<T>(v: &mut Vec<T>, n: usize) -> Result<(), i64> {
    v.clear();
    v.try_reserve(n).map_err(|_| NO_MEMORY)
}

#[inline(always)]
fn pitch(y: f64) -> f64 {
    (y + 0.5).floor()
}

#[inline(always)]
fn on_edge(y: f64) -> bool {
    (y + 0.5 - rint(y + 0.5)).abs() < ROW_EDGE
}

/// paths.direction_changes: the segments (i = point i to i + 1) going the other way than the last one that moved.
#[inline(always)]
fn direction_changes(v: &[f64], out: &mut Vec<usize>) -> Result<(), i64> {
    out.clear();
    let mut last = 0.0f64;
    for i in 0..v.len().saturating_sub(1) {
        let d = v[i + 1] - v[i];
        let s = if d > 0.0 { 1.0 } else if d < 0.0 { -1.0 } else { 0.0 };
        if s != 0.0 {
            if last != 0.0 && s != last {
                grow(out, i)?;
            }
            last = s;
        }
    }
    Ok(())
}

/// paths._remap (end_dot off).
#[inline(always)]
fn remap(ys: &mut [f64], i: usize, j: usize, move_start: bool, move_end: bool) {
    let (y0, y1) = (ys[i], ys[j]);
    let (ps, pe) = (pitch(y0), pitch(y1));
    if ps == pe {
        return;
    }
    let d = if pe > ps { 1.0 } else { -1.0 };
    let ns = if move_start { ps - d * EDGE } else { y0 };
    let ne = if move_end { pe + d * EDGE } else { y1 };
    let k = (ne - ns) / (y1 - y0);
    for t in i..=j {
        ys[t] = ns + (ys[t] - y0) * k;
    }
}

/// paths.edges_settled on (x, y) in place (y2: room for a copy).
#[inline(always)]
fn edges_settled(x: &[f64], y: &mut Vec<f64>, y2: &mut Vec<f64>, starts: &mut Vec<usize>, run: &mut Vec<usize>)
                 -> Result<(), i64> {
    let n = y.len();
    if !y.iter().any(|&v| on_edge(v)) {
        return Ok(());
    }
    fit(run, n)?;
    fit(starts, 8)?;
    for i in 0..n {
        if i == 0 || (y[i] - y[i - 1]).abs() > ROW_EDGE {
            grow(starts, i)?;
        }
        run.push(starts.len() - 1);
    }
    let ns = starts.len();
    let end_of = |r: usize| if r + 1 < ns { starts[r + 1] - 1 } else { n - 1 };
    let closed = n > 2 && x[0] == x[n - 1] && y[0] == y[n - 1] && ns > 1;
    fit(y2, n)?;
    y2.extend_from_slice(y);
    for i in 0..n {
        let v = y[i];
        if !on_edge(v) {
            continue;
        }
        let r = run[i];
        let mut prev = starts[r] as i64 - 1;
        let mut nxt = end_of(r) as i64 + 1;
        if closed {
            if r == 0 {
                prev = starts[ns - 1] as i64 - 1;
            }
            if r == run[n - 1] {
                nxt = end_of(0) as i64 + 1;
            }
        }
        let on = rint(v + 0.5) - 0.5;
        let (has_p, has_n) = (prev >= 0, nxt < n as i64);
        let below = (!has_p || y[prev.max(0) as usize] < on) && (!has_n || y[nxt.min(n as i64 - 1) as usize] < on)
            && (has_p || has_n);
        y2[i] = if below { on - 2.0 * ROW_EDGE } else { on };
    }
    std::mem::swap(y, y2);
    Ok(())
}

struct Bufs {
    p: Vec<i64>,
    et: Vec<f64>,
    ep: Vec<i64>,
    st: Vec<i64>,
    nx: Vec<i64>,
}

/// paths.parts_notes for one part q[f..=l] (no tails): its notes onto out (start, end, key rows).
#[inline(always)]
fn part_notes(t: &[f64], y: &[f64], f: usize, l: usize, b: &mut Bufs, out: &mut Vec<i64>) -> Result<(), i64> {
    fit(&mut b.p, l - f + 1)?;
    for i in f..=l {
        let v = y[i];
        let mut p = pitch(v) as i64;
        if on_edge(v) {
            p = rint(v + 0.5) as i64;
            if f != l && (i == f || y[i - 1] < v - ROW_EDGE) && (i == l || y[i + 1] < v - ROW_EDGE) {
                p -= 1;
            }
        }
        b.p.push(p);
    }
    // entries: the part's first point, then its crossings in order
    let mut m = 1usize;
    for j in f..l {
        m += (b.p[j + 1 - f] - b.p[j - f]).unsigned_abs() as usize;
    }
    fit(&mut b.et, m)?;
    fit(&mut b.ep, m)?;
    b.et.push(t[f]);
    b.ep.push(b.p[0]);
    for j in f..l {
        let (pa, pb) = (b.p[j - f], b.p[j + 1 - f]);
        if pa == pb {
            continue;
        }
        let step = if pb > pa { 1i64 } else { -1 };
        let half = 0.5 * step as f64;
        let (ta, tb, ya, yb) = (t[j], t[j + 1], y[j], y[j + 1]);
        let mut qq = pa;
        while qq != pb {
            qq += step;
            let yy = qq as f64 - half;
            b.et.push(ta + (tb - ta) * (yy - ya) / (yb - ya));
            b.ep.push(qq);
        }
    }
    fit(&mut b.st, m)?;
    for &e in b.et.iter() {
        b.st.push((e + 0.5).floor() as i64);
    }
    let end_tick = (t[l] + 0.5).floor() as i64;
    let first_tick = (t[f] + 0.5).floor() as i64;
    // a note lasts until the next one starting on a later tick (the part's end after its last)
    fit(&mut b.nx, m)?;
    b.nx.resize(m, 0);
    let mut nxt = end_tick;
    for i in (0..m).rev() {
        if i + 1 < m && b.st[i + 1] > b.st[i] {
            nxt = b.st[i + 1];
        }
        b.nx[i] = nxt;
    }
    out.try_reserve(3 * m).map_err(|_| NO_MEMORY)?;
    for i in 0..m {
        let s = b.st[i];
        let mut e = b.nx[i].max(s + 1);
        if i + 1 < m && b.st[i + 1] == s {
            e = s + 1;
        }
        if s == end_tick && first_tick < end_tick {
            out.extend_from_slice(&[s - 1, s, b.ep[i]]);
        } else {
            out.extend_from_slice(&[s, e, b.ep[i]]);
        }
    }
    Ok(())
}

/// custom.paths_outline before keep_longest: pts = every path's (beat, key) points one after another (x, y, x, y...),
/// path k = points offs[k] .. offs[k + 1], closed[k] = custom.path_closed; ppq -> (notes as (n, 3) rows,).
#[inline(always)]
pub fn outline_notes(pts: &[f64], offs: &[i64], closed: &[u8], ppq: f64) -> Outs {
    if offs.is_empty() || pts.len() % 2 != 0 || !ppq.is_finite() {
        return Err(FAILED);
    }
    let np_ = offs.len() - 1;
    let total = pts.len() / 2;
    if closed.len() < np_ || offs[0] != 0 || offs[np_] != total as i64 || offs.windows(2).any(|w| w[1] <= w[0]) {
        return Err(FAILED);
    }
    for i in 0..total {
        if !((pts[2 * i] * ppq).abs() < BIG && pts[2 * i].abs() < BIG && pts[2 * i + 1].abs() < KEYS_BIG) {
            return Err(FAILED);
        }
    }
    let (mut xs, mut ys, mut y2) = (Vec::new(), Vec::new(), Vec::new());
    let (mut qx, mut qy) = (Vec::new(), Vec::new());
    let (mut cuts, mut starts, mut run) = (Vec::new(), Vec::new(), Vec::new());
    let mut b = Bufs { p: Vec::new(), et: Vec::new(), ep: Vec::new(), st: Vec::new(), nx: Vec::new() };
    let mut mine: Vec<i64> = Vec::new();
    let mut keys: Vec<i64> = Vec::new();
    let mut out: Vec<i64> = Vec::new();
    out.try_reserve(3 * total).map_err(|_| NO_MEMORY)?;
    for k in 0..np_ {
        let (a, z) = (offs[k] as usize, offs[k + 1] as usize);
        // paths.dedupe
        fit(&mut xs, z - a + 1)?;
        fit(&mut ys, z - a + 1)?;
        for i in a..z {
            let (x, y) = (pts[2 * i], pts[2 * i + 1]);
            if i == a || x != pts[2 * i - 2] || y != pts[2 * i - 1] {
                xs.push(x);
                ys.push(y);
            }
        }
        let n = xs.len();
        if n < 2 {
            let t = (xs[0] * ppq + 0.5).floor() as i64;
            out.try_reserve(3).map_err(|_| NO_MEMORY)?;
            out.extend_from_slice(&[t, t + 1, pitch(ys[0]) as i64]);
            continue;
        }
        if closed[k] != 0 {
            // paths.loop_from_left: restarted at its leftmost point (then the lowest, then the first)
            if !(xs[n - 1] == xs[0] && ys[n - 1] == ys[0]) {
                xs.push(xs[0]);
                ys.push(ys[0]);
            }
            let m = xs.len() - 1;
            let mut i0 = 0usize;
            for i in 1..m {
                if xs[i] < xs[i0] || (xs[i] == xs[i0] && ys[i] < ys[i0]) {
                    i0 = i;
                }
            }
            fit(&mut qx, m + 1)?;
            fit(&mut qy, m + 1)?;
            for i in (i0..m).chain(0..=i0) {
                qx.push(xs[i]);
                qy.push(ys[i]);
            }
            std::mem::swap(&mut xs, &mut qx);
            std::mem::swap(&mut ys, &mut qy);
        } else {
            if xs[n - 1] < xs[0] {
                xs.reverse();
                ys.reverse();
            }
            // paths.stretch_ends (end_dot off)
            direction_changes(&ys, &mut cuts)?;
            if cuts.is_empty() {
                remap(&mut ys, 0, n - 1, true, true);
            } else {
                let (c0, c1) = (cuts[0], cuts[cuts.len() - 1]);
                remap(&mut ys, 0, c0, true, false);
                remap(&mut ys, c1, n - 1, false, true);
            }
        }
        for x in xs.iter_mut() {
            *x *= ppq;
        }
        // paths.line_notes (no tail)
        edges_settled(&xs, &mut ys, &mut y2, &mut starts, &mut run)?;
        let n = xs.len();
        direction_changes(&xs, &mut cuts)?;
        fit(&mut qx, n + cuts.len())?;
        fit(&mut qy, n + cuts.len())?;
        mine.clear();
        let pieces = cuts.len() + 1;
        for pc in 0..pieces {
            let lo = if pc == 0 { 0 } else { cuts[pc - 1] };
            let hi = if pc < cuts.len() { cuts[pc] } else { n - 1 };
            let f = qx.len();
            if xs[hi] < xs[lo] {
                for i in (lo..=hi).rev() {
                    qx.push(xs[i]);
                    qy.push(ys[i]);
                }
            } else {
                for i in lo..=hi {
                    qx.push(xs[i]);
                    qy.push(ys[i]);
                }
            }
            let l = qx.len() - 1;
            // its parts: cut where it turns upright / stops being upright
            let up = |i: usize| (qx[i + 1] - qx[i]).abs() < 1e-9;
            let mut cur = f;
            for s in f + 1..l {
                if up(s) != up(s - 1) {
                    part_notes(&qx, &qy, cur, s, &mut b, &mut mine)?;
                    cur = s;
                }
            }
            part_notes(&qx, &qy, cur, l, &mut b, &mut mine)?;
        }
        // notes starting on the path's last tick end there instead (a key already reaching it: no second note)
        let (mut lo_x, mut hi_x) = (xs[0], xs[0]);
        for &x in xs.iter() {
            if x < lo_x {
                lo_x = x;
            }
            if x > hi_x {
                hi_x = x;
            }
        }
        let (right, left) = ((hi_x + 0.5).floor(), (lo_x + 0.5).floor());
        let rows = mine.len() / 3;
        let ri = right as i64;
        let late = |s: i64| s as f64 >= right;
        if right > left && (0..rows).any(|r| late(mine[3 * r])) {
            keys.clear();
            for r in 0..rows {
                let (s, e) = (mine[3 * r], mine[3 * r + 1]);
                if !late(s) && e >= ri && s < ri - 1 {
                    grow(&mut keys, mine[3 * r + 2])?;
                }
            }
            keys.sort_unstable();
            out.try_reserve(mine.len()).map_err(|_| NO_MEMORY)?;
            for r in 0..rows {
                let (s, key) = (mine[3 * r], mine[3 * r + 2]);
                if late(s) {
                    if keys.binary_search(&key).is_err() {
                        out.extend_from_slice(&[ri - 1, ri, key]);
                    }
                } else {
                    out.extend_from_slice(&mine[3 * r..3 * r + 3]);
                }
            }
        } else {
            out.try_reserve(mine.len()).map_err(|_| NO_MEMORY)?;
            out.extend_from_slice(&mine);
        }
    }
    Ok(vec![Out::I64(out, 3)])
}
