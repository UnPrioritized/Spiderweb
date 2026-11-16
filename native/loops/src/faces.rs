//! Exact areas of a drawing's lines (notes/faces.py Faces): fastloops' face_build. The crossing rounds give the very
//! same numbers as faces.py's NumPy code (the same sums in the same order; each slab's lines by their piece's middle,
//! then line number); the joins don't depend on order (which spots are left of a gap, each group's smallest piece,
//! fewest lines crossed). Slabs / levels shared out on half the cores when many.
use std::cmp::{Ordering, Reverse};
use std::collections::BinaryHeap;

use rayon::prelude::*;

use crate::pyext::{Out, Outs};
use crate::{filled, grow, room, zeros, FAILED, NO_MEMORY};

/// One line's piece in one slab.
#[derive(Clone, Copy, Default)]
struct P {
    mid: f64,
    xb: f64,
    xt: f64,
    e: i64,
}

/// The lines (lower end first).
struct Lines<'a> {
    x0: &'a [f64],
    y0: &'a [f64],
    x1: &'a [f64],
    y1: &'a [f64],
}

impl Lines<'_> {
    /// faces.Faces._x: line e's x at height y.
    #[inline(always)]
    fn at(&self, e: usize, y: f64) -> f64 {
        self.x0[e] + (self.x1[e] - self.x0[e]) * (y - self.y0[e]) / (self.y1[e] - self.y0[e])
    }

    #[inline(always)]
    fn piece(&self, e: usize, lo: f64, hi: f64) -> P {
        let (xb, xt) = (self.at(e, lo), self.at(e, hi));
        P { mid: (xb + xt) / 2.0, xb, xt, e: e as i64 }
    }
}

/// NumPy's order for floats (finite here): -0.0 and 0.0 alike.
#[inline(always)]
fn cmp_f(a: f64, b: f64) -> Ordering {
    if a < b {
        Ordering::Less
    } else if a > b {
        Ordering::Greater
    } else {
        Ordering::Equal
    }
}

/// np.searchsorted(lv, y) ("left"): how many of lv are below y.
#[inline(always)]
fn below(lv: &[f64], y: f64) -> usize {
    let (mut a, mut b) = (0usize, lv.len());
    while a < b {
        let m = (a + b) >> 1;
        if lv[m] < y {
            a = m + 1;
        } else {
            b = m;
        }
    }
    a
}

/// A slab's pieces in order: by middle, then line number (np.lexsort's stable order on line-ordered input).
#[inline(always)]
fn sort_slab(ps: &mut [P]) {
    ps.sort_unstable_by(|p, q| cmp_f(p.mid, q.mid).then(p.e.cmp(&q.e)));
}

/// Slab numbers where blocks of about the same number of pieces start (first 0, last ns): at most `parts` blocks.
fn block_cuts(start: &[usize], parts: usize) -> Result<Vec<usize>, i64> {
    let ns = start.len() - 1;
    let total = start[ns];
    let mut cuts = room(parts + 1)?;
    cuts.push(0usize);
    let per = (total / parts.max(1)).max(1);
    let mut next = per;
    for s in 1..ns {
        if start[s] >= next && cuts.len() < parts {
            cuts.push(s);
            next = start[s] + per;
        }
    }
    cuts.push(ns);
    Ok(cuts)
}

/// ps split at those blocks' pieces: (first slab, its end, the block's pieces).
fn split_blocks<'a, T>(mut ps: &'a mut [T], start: &[usize], cuts: &[usize]) -> Result<Vec<(usize, usize, &'a mut [T])>, i64> {
    let mut out = room(cuts.len())?;
    let mut at = 0usize;
    for w in cuts.windows(2) {
        let (a, b) = (w[0], w[1]);
        let (head, rest) = ps.split_at_mut(start[b] - at);
        at = start[b];
        out.push((a, b, head));
        ps = rest;
    }
    Ok(out)
}

/// The pool for this much work, how many blocks to share it in.
#[inline(always)]
fn pool(many: bool, n: usize) -> Option<&'static rayon::ThreadPool> {
    crate::pool_for(many, n, 2)
}

/// Every line in every slab it runs through (faces.Faces._pairs): pieces slab by slab, each slab in order.
fn first_pairs(ln: &Lines, lv: &[f64], many: bool) -> Result<(Vec<P>, Vec<usize>), i64> {
    let n = ln.x0.len();
    let ns = lv.len() - 1;
    let mut start: Vec<usize> = zeros(ns + 1)?;
    let mut ab: Vec<usize> = zeros(2 * n)?;
    for i in 0..n {
        let (a, b) = (below(lv, ln.y0[i]), below(lv, ln.y1[i]).min(ns));
        ab[2 * i] = a;
        ab[2 * i + 1] = b;
        for s in a..b {
            start[s + 1] += 1;
        }
    }
    for s in 0..ns {
        start[s + 1] += start[s];
    }
    let mut ps: Vec<P> = filled(start[ns], P::default())?;
    let mut cur: Vec<usize> = zeros(ns)?;
    cur.copy_from_slice(&start[..ns]);
    for i in 0..n {
        for s in ab[2 * i]..ab[2 * i + 1] {
            ps[cur[s]] = ln.piece(i, lv[s], lv[s + 1]);
            cur[s] += 1;
        }
    }
    sort_all(&mut ps, &start, many)?;
    Ok((ps, start))
}

/// Every slab's pieces sorted.
fn sort_all(ps: &mut [P], start: &[usize], many: bool) -> Result<(), i64> {
    match pool(many, ps.len()) {
        None => {
            for s in 0..start.len() - 1 {
                sort_slab(&mut ps[start[s]..start[s + 1]]);
            }
        }
        Some(pl) => {
            let cuts = block_cuts(start, pl.current_num_threads() * 4)?;
            let blocks = split_blocks(ps, start, &cuts)?;
            pl.install(|| {
                blocks.into_par_iter().for_each(|(a, b, part)| {
                    let base = start[a];
                    for s in a..b {
                        sort_slab(&mut part[start[s] - base..start[s + 1] - base]);
                    }
                })
            });
        }
    }
    Ok(())
}

/// Heights where neighbours in a slab cross inside it (fastloops.face_build's round), and whether any are "bad"
/// (further apart than eps at either end, the wrong way round).
fn crossings_in(ps: &[P], start: &[usize], lv: &[f64], eps: f64, a: usize, b: usize) -> Result<(bool, Vec<f64>), i64> {
    let base = start[a];
    let mut ys = Vec::new();
    let mut bad = false;
    for s in a..b {
        let (c0, c1) = (start[s] - base, start[s + 1] - base);
        for i in c0..c1.saturating_sub(1) {
            let (p, q) = (&ps[i], &ps[i + 1]);
            let (db, dt) = (p.xb - q.xb, p.xt - q.xt);
            if db > eps || dt > eps {
                bad = true;
                if db - dt != 0.0 {
                    let t = db / (db - dt);
                    if t > 0.0 && t < 1.0 {
                        grow(&mut ys, lv[s] + t * (lv[s + 1] - lv[s]))?;
                    }
                }
            }
        }
    }
    Ok((bad, ys))
}

fn crossings(ps: &[P], start: &[usize], lv: &[f64], eps: f64, many: bool) -> Result<(bool, Vec<f64>), i64> {
    let ns = start.len() - 1;
    match pool(many, ps.len()) {
        None => crossings_in(ps, start, lv, eps, 0, ns),
        Some(pl) => {
            let cuts = block_cuts(start, pl.current_num_threads() * 4)?;
            let got: Vec<Result<(bool, Vec<f64>), i64>> = pl.install(|| {
                cuts.par_windows(2).map(|w| crossings_in(&ps[start[w[0]]..start[w[1]]], &start[w[0]..=w[1]], &lv[w[0]..=w[1]], eps, 0, w[1] - w[0])).collect()
            });
            let mut bad = false;
            let mut all = Vec::new();
            for g in got {
                let (b, ys) = g?;
                bad |= b;
                all.try_reserve(ys.len()).map_err(|_| NO_MEMORY)?;
                all.extend_from_slice(&ys);
            }
            Ok((bad, all))
        }
    }
}

/// np.unique(lv + ys): lv is sorted with no repeats.
fn merged(lv: &[f64], mut ys: Vec<f64>) -> Result<Vec<f64>, i64> {
    ys.sort_unstable_by(|a, b| cmp_f(*a, *b));
    let mut out = room(lv.len() + ys.len())?;
    let (mut i, mut j) = (0usize, 0usize);
    while i < lv.len() || j < ys.len() {
        let v = if j >= ys.len() || (i < lv.len() && lv[i] <= ys[j]) {
            i += 1;
            lv[i - 1]
        } else {
            j += 1;
            ys[j - 1]
        };
        if out.last().is_none_or(|&l: &f64| l != v) {
            out.push(v);
        }
    }
    Ok(out)
}

/// What split works from (old pieces, their slabs, old slab s = new slabs a[s] .. a[s + 1], the new levels).
struct Split<'a> {
    ln: &'a Lines<'a>,
    ps: &'a [P],
    start: &'a [usize],
    a: &'a [usize],
    nstart: &'a [usize],
    new: &'a [f64],
}

impl Split<'_> {
    /// Old slab s's pieces into part (which starts at new piece base): copied, or each new slab worked out + sorted.
    #[inline(always)]
    fn one(&self, s: usize, part: &mut [P], base: usize) {
        let old = &self.ps[self.start[s]..self.start[s + 1]];
        let (a, nstart, new) = (self.a, self.nstart, self.new);
        if a[s + 1] - a[s] == 1 {
            let k = a[s];
            part[nstart[k] - base..nstart[k + 1] - base].copy_from_slice(old);
            return;
        }
        for k in a[s]..a[s + 1] {
            let dst = &mut part[nstart[k] - base..nstart[k + 1] - base];
            for (d, p) in dst.iter_mut().zip(old.iter()) {
                *d = self.ln.piece(p.e as usize, new[k], new[k + 1]);
            }
            sort_slab(dst);
        }
    }
}

/// The pieces after new levels went in (faces.Faces._split): slabs that got cut worked out anew, the rest copied.
fn split(ln: &Lines, ps: &[P], start: &[usize], lv: &[f64], new: &[f64], many: bool) -> Result<(Vec<P>, Vec<usize>), i64> {
    let ns = lv.len() - 1;
    let ns2 = new.len() - 1;
    let mut a: Vec<usize> = zeros(ns + 1)?; // (old slab s = new slabs a[s] .. a[s + 1])
    for s in 0..=ns {
        a[s] = below(new, lv[s]);
    }
    let mut nstart: Vec<usize> = zeros(ns2 + 1)?;
    for s in 0..ns {
        let cnt = start[s + 1] - start[s];
        for k in a[s]..a[s + 1] {
            nstart[k + 1] = cnt;
        }
    }
    for k in 0..ns2 {
        nstart[k + 1] += nstart[k];
    }
    let mut out: Vec<P> = filled(nstart[ns2], P::default())?;
    let sp = Split { ln, ps, start, a: &a, nstart: &nstart, new };
    match pool(many, out.len()) {
        None => {
            for s in 0..ns {
                sp.one(s, &mut out, 0);
            }
        }
        Some(pl) => {
            let cuts = block_cuts(start, pl.current_num_threads() * 4)?;
            // (the same blocks of old slabs: their new pieces sit together in the same order)
            let mut ncut: Vec<usize> = zeros(cuts.len())?;
            for (i, &c) in cuts.iter().enumerate() {
                ncut[i] = a[c];
            }
            let blocks = split_blocks(&mut out, &nstart, &ncut)?;
            pl.install(|| {
                blocks.into_par_iter().zip(cuts.par_windows(2)).for_each(|((ka, _, part), w)| {
                    let base = nstart[ka];
                    for s in w[0]..w[1] {
                        sp.one(s, part, base);
                    }
                })
            });
        }
    }
    Ok((out, nstart))
}

/// Joins found at the levels lo .. hi: (pieces that are one area, depth steps (a, b, lines crossed)).
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn level_joins(ps: &[P], start: &[usize], off: &[i64], out: i64, hfirst: &[usize], hx: &[(f64, f64)], eps: f64,
               lo: usize, hi: usize) -> Result<(Vec<(i64, i64)>, Vec<(i64, i64, i64)>), i64> {
    let ns = start.len() - 1;
    let mut joins = Vec::new();
    let mut steps = Vec::new();
    let mut spots: Vec<(f64, u8)> = Vec::new();
    for k in lo..hi {
        spots.clear();
        let need = if k > 0 { start[k] - start[k - 1] } else { 0 } + if k < ns { start[k + 1] - start[k] } else { 0 }
            + 2 * (hfirst[k + 1] - hfirst[k]);
        if need < 2 {
            continue;
        }
        spots.try_reserve(need).map_err(|_| NO_MEMORY)?;
        if k > 0 {
            spots.extend(ps[start[k - 1]..start[k]].iter().map(|p| (p.xt, 0u8)));
        }
        if k < ns {
            spots.extend(ps[start[k]..start[k + 1]].iter().map(|p| (p.xb, 1u8)));
        }
        for &(x0, x1) in &hx[hfirst[k]..hfirst[k + 1]] {
            spots.push((x0, 2));
            spots.push((x1, 3));
        }
        spots.sort_unstable_by(|p, q| cmp_f(p.0, q.0));
        let (mut nb, mut na, mut cover) = (0i64, 0i64, 0i64);
        for i in 0..spots.len() - 1 {
            match spots[i].1 {
                0 => nb += 1,
                1 => na += 1,
                2 => cover += 1,
                _ => cover -= 1,
            }
            if spots[i + 1].0 - spots[i].0 > eps {
                let pb = if k > 0 { off[k - 1] + nb } else { out };
                let pa = if k < ns { off[k] + na } else { out };
                if cover == 0 {
                    grow(&mut joins, (pb, pa))?;
                } else if cover > 0 {
                    grow(&mut steps, (pb, pa, cover))?;
                }
            }
        }
    }
    Ok((joins, steps))
}

#[inline(always)]
fn root(head: &mut [i64], mut i: i64) -> i64 {
    while head[i as usize] != i {
        let h = head[head[i as usize] as usize];
        head[i as usize] = h;
        i = h;
    }
    i
}

#[inline(always)]
fn join(head: &mut [i64], a: i64, b: i64) {
    let (ra, rb) = (root(head, a), root(head, b));
    if ra != rb {
        head[ra.max(rb) as usize] = ra.min(rb);
    }
}

/// fastloops.face_build: sloped lines (0 = the lower end), the first levels, level lines (height, left, right), eps
/// -> (levels, line + slab of every piece slab by slab, each piece's area, each one's depth).
#[allow(clippy::too_many_arguments)]
pub fn face_build(x0: &[f64], y0: &[f64], x1: &[f64], y1: &[f64], levels: &[f64], hy: &[f64], hx0: &[f64],
                  hx1: &[f64], eps: f64, many: bool) -> Outs {
    let n = x0.len();
    let nh = hy.len();
    if y0.len() != n || x1.len() != n || y1.len() != n || hx0.len() != nh || hx1.len() != nh || levels.len() < 2
        || !eps.is_finite()
    {
        return Err(FAILED);
    }
    let finite = |s: &[f64]| s.iter().all(|v| v.is_finite());
    if !(finite(x0) && finite(y0) && finite(x1) && finite(y1) && finite(levels) && finite(hy) && finite(hx0)
        && finite(hx1))
        || (0..n).any(|i| y1[i] <= y0[i])
        || levels.windows(2).any(|w| w[1] <= w[0])
    {
        return Err(FAILED); // (odd input: faces.py's own NumPy way)
    }
    let ln = Lines { x0, y0, x1, y1 };
    let mut lv = crate::copied(levels)?;
    let (mut ps, mut start) = first_pairs(&ln, &lv, many)?;
    for _ in 0..500 {
        let (bad, ys) = crossings(&ps, &start, &lv, eps, many)?;
        if !bad {
            break;
        }
        let new = merged(&lv, ys)?;
        if new.len() == lv.len() {
            break;
        }
        let (p2, s2) = split(&ln, &ps, &start, &lv, &new, many)?;
        ps = p2;
        start = s2;
        lv = new;
    }
    let ns = lv.len() - 1;
    let nl = ns + 1;
    let mut off: Vec<i64> = zeros(ns + 1)?;
    for s in 0..ns {
        off[s + 1] = off[s] + (start[s + 1] - start[s]) as i64 + 1;
    }
    let out = off[ns];
    let np_ = (out + 1) as usize;
    // level lines by their level
    let mut hk: Vec<(usize, f64, f64)> = room(nh)?;
    for i in 0..nh {
        hk.push((below(&lv, hy[i]), hx0[i], hx1[i]));
    }
    hk.sort_unstable_by(|p, q| p.0.cmp(&q.0).then(cmp_f(p.1, q.1)).then(cmp_f(p.2, q.2)));
    let mut hfirst: Vec<usize> = zeros(nl + 1)?;
    for &(k, _, _) in &hk {
        hfirst[k.min(nl - 1) + 1] += 1;
    }
    for k in 0..nl {
        hfirst[k + 1] += hfirst[k];
    }
    let mut hx: Vec<(f64, f64)> = room(nh)?;
    hx.extend(hk.iter().map(|&(_, a, b)| (a, b)));
    // joins at the levels (shared out by blocks of levels)
    let parts: Vec<Result<(Vec<(i64, i64)>, Vec<(i64, i64, i64)>), i64>> = match pool(many, ps.len()) {
        None => {
            let mut v = room(1)?;
            v.push(level_joins(&ps, &start, &off, out, &hfirst, &hx, eps, 0, nl));
            v
        }
        Some(pl) => {
            let mut cuts = block_cuts(&start, pl.current_num_threads() * 4)?;
            if let Some(l) = cuts.last_mut() {
                *l = nl; // (the top level too)
            }
            pl.install(|| {
                cuts.par_windows(2).map(|w| level_joins(&ps, &start, &off, out, &hfirst, &hx, eps, w[0], w[1])).collect()
            })
        }
    };
    let mut head: Vec<i64> = zeros(np_)?;
    for (i, h) in head.iter_mut().enumerate() {
        *h = i as i64;
    }
    for s in 0..ns {
        // (each slab's leftmost and rightmost piece: outside)
        join(&mut head, off[s], out);
        join(&mut head, off[s] + (start[s + 1] - start[s]) as i64, out);
    }
    let mut steps: Vec<(i64, i64, i64)> = room(ps.len())?;
    for s in 0..ns {
        for r in 0..(start[s + 1] - start[s]) as i64 {
            steps.push((off[s] + r, off[s] + r + 1, 1));
        }
    }
    for p in parts {
        let (j, st) = p?;
        for (a, b) in j {
            join(&mut head, a, b);
        }
        steps.try_reserve(st.len()).map_err(|_| NO_MEMORY)?;
        steps.extend_from_slice(&st);
    }
    let mut lab: Vec<i64> = zeros(np_)?;
    for i in 0..np_ {
        lab[i] = root(&mut head, i as i64);
    }
    drop(head);
    // fewest lines to cross from outside: shortest paths between the groups
    let mut deg: Vec<usize> = zeros(np_ + 1)?;
    for &(a, b, _) in &steps {
        let (a, b) = (lab[a as usize] as usize, lab[b as usize] as usize);
        if a != b {
            deg[a + 1] += 1;
            deg[b + 1] += 1;
        }
    }
    for i in 0..np_ {
        deg[i + 1] += deg[i];
    }
    let mut to: Vec<(usize, i64)> = filled(deg[np_], (0usize, 0i64))?;
    let mut fill: Vec<usize> = zeros(np_)?;
    fill.copy_from_slice(&deg[..np_]);
    for &(a, b, w) in &steps {
        let (a, b) = (lab[a as usize] as usize, lab[b as usize] as usize);
        if a != b {
            to[fill[a]] = (b, w);
            fill[a] += 1;
            to[fill[b]] = (a, w);
            fill[b] += 1;
        }
    }
    drop(steps);
    drop(fill);
    let mut depth: Vec<i64> = filled(np_, 2305843009213693951i64)?; // (np.iinfo(np.int64).max // 4, as faces.py)
    let o = lab[out as usize] as usize;
    depth[o] = 0;
    let mut heap: BinaryHeap<Reverse<(i64, usize)>> = BinaryHeap::new();
    heap.try_reserve(1).map_err(|_| NO_MEMORY)?;
    heap.push(Reverse((0, o)));
    while let Some(Reverse((d, a))) = heap.pop() {
        if d > depth[a] {
            continue;
        }
        for &(b, w) in &to[deg[a]..deg[a + 1]] {
            if d + w < depth[b] {
                depth[b] = d + w;
                heap.try_reserve(1).map_err(|_| NO_MEMORY)?;
                heap.push(Reverse((d + w, b)));
            }
        }
    }
    let cap = np_ as i64 * 4;
    for d in depth.iter_mut() {
        if *d > cap {
            *d = 0; // (cut off from everything: shouldn't happen)
        }
    }
    let mut e_out: Vec<i64> = zeros(ps.len())?;
    let mut s_out: Vec<i64> = zeros(ps.len())?;
    for s in 0..ns {
        for i in start[s]..start[s + 1] {
            e_out[i] = ps[i].e;
            s_out[i] = s as i64;
        }
    }
    Ok(vec![Out::F64(lv, 0), Out::I64(e_out, 0), Out::I64(s_out, 0), Out::I64(lab, 0), Out::I64(depth, 0)])
}
