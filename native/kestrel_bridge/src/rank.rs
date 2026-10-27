//! Each block's note-ons put in PRIORITY ORDER when they are pushed (on the caller's thread), so the render
//! thread only cuts the order at its allowance: tiers + fair-by-track choice on the render thread took ~5 ms of a
//! 10.7 ms block in Shinki's dense part (185,000 note-ons a block), which held the allowance at its floor.
//!
//! The order (budget.rs has the user's rules): tier 1 = one start of each key (the longest, then the loudest),
//! tier 2 = one of each key on each channel, tier 3 = the rest. Inside tiers 2 and 3 the tracks take TURNS, each
//! track loudest first (earlier first at one velocity): any first N of a round-robin is the fair split (a
//! track with fewer notes runs out and leaves its turns to the others). Each note-on also gets the ranks of its
//! key's two kept candidates, where its loudness goes if it is dropped (`srep` same channel + key, `krep` the
//! key's tier-1 note, always kept).
//!
//! A block is ranked from the events of ONE push with frames in it, in a row. A block whose note-ons came in
//! two pushes, or late into a later block, is told apart by `group` and chosen on the render thread instead.

use crate::Pending;

/// `Pending::group` of an event that wasn't ranked (not a note-on).
pub const NO_GROUP: u64 = 0;

pub struct Ranker {
    serial: u64,
    by_key: [usize; 128],
    by_slot: Vec<usize>,
    ons: Vec<usize>,
    t1: Vec<usize>,
    t2: Vec<usize>,
    t3: Vec<usize>,
    order: Vec<usize>,
    // round-robin scratch
    per_track: Vec<u32>,
    starts: Vec<u32>,
    tracks: Vec<u16>,
    sorted: Vec<usize>,
    by_vel: Vec<usize>,
}

impl Ranker {
    pub fn new() -> Self {
        Ranker {
            serial: 0,
            by_key: [usize::MAX; 128],
            by_slot: vec![usize::MAX; 256 * 128],
            ons: Vec::new(),
            t1: Vec::new(),
            t2: Vec::new(),
            t3: Vec::new(),
            order: Vec::new(),
            per_track: vec![0; 1 << 16],
            starts: vec![0; 1 << 16],
            tracks: Vec::new(),
            sorted: Vec::new(),
            by_vel: Vec::new(),
        }
    }

    /// Rank every block's note-ons in `batch` (frames in a row of one block = one group).
    pub fn rank(&mut self, batch: &mut [Pending], block_frames: u64) {
        let mut a = 0;
        while a < batch.len() {
            let block = batch[a].frame / block_frames;
            let mut b = a + 1;
            while b < batch.len() && batch[b].frame / block_frames == block {
                b += 1;
            }
            self.serial += 1;
            self.rank_group(&mut batch[a..b], self.serial);
            a = b;
        }
    }

    fn rank_group(&mut self, g: &mut [Pending], group: u64) {
        self.ons.clear();
        for (i, p) in g.iter().enumerate() {
            if p.msg & 0xF0 == 0x90 && (p.msg >> 16) & 0x7F != 0 {
                self.ons.push(i);
            }
        }
        if self.ons.is_empty() {
            return;
        }
        let key = |p: &Pending| ((p.msg >> 8) & 0x7F) as usize;
        let slot = |p: &Pending| ((((p.msg >> 24) & 0x0F) << 4 | (p.msg & 0x0F)) as usize) * 128 + key(p);
        let rank = |p: &Pending| (p.len, (p.msg >> 16) & 0x7F);
        for &i in &self.ons {
            let k = &mut self.by_key[key(&g[i])];
            if *k == usize::MAX || rank(&g[i]) > rank(&g[*k]) {
                *k = i;
            }
            let s = &mut self.by_slot[slot(&g[i])];
            if *s == usize::MAX || rank(&g[i]) > rank(&g[*s]) {
                *s = i;
            }
        }
        self.t1.clear();
        self.t2.clear();
        self.t3.clear();
        for &i in &self.ons {
            if self.by_key[key(&g[i])] == i {
                self.t1.push(i);
            } else if self.by_slot[slot(&g[i])] == i {
                self.t2.push(i);
            } else {
                self.t3.push(i);
            }
        }
        self.order.clear();
        let t1 = std::mem::take(&mut self.t1);
        self.order.extend_from_slice(&t1);
        self.t1 = t1;
        let t2 = std::mem::take(&mut self.t2);
        self.round_robin(g, &t2);
        self.t2 = t2;
        let t3 = std::mem::take(&mut self.t3);
        self.round_robin(g, &t3);
        self.t3 = t3;
        for (r, &i) in self.order.iter().enumerate() {
            g[i].rank = r as u32;
            g[i].group = group;
        }
        for &i in &self.ons {
            let (s, k) = (self.by_slot[slot(&g[i])], self.by_key[key(&g[i])]);
            g[i].srep = g[s].rank;
            g[i].krep = g[k].rank;
            g[i].s_same = g[s].frame == g[i].frame;
            g[i].k_same = g[k].frame == g[i].frame;
        }
        for &i in &self.ons {
            self.by_key[key(&g[i])] = usize::MAX;
            self.by_slot[slot(&g[i])] = usize::MAX;
        }
    }

    /// Append `list` to `order`: tracks taking turns, each track's notes loudest first (stable). Counting sorts
    /// only (a dense block has ~200,000).
    fn round_robin(&mut self, g: &[Pending], list: &[usize]) {
        if list.is_empty() {
            return;
        }
        // loudest first, stable
        let mut hist = [0u32; 129];
        for &i in list {
            hist[127 - ((g[i].msg >> 16) & 0x7F) as usize + 1] += 1;
        }
        for v in 1..129 {
            hist[v] += hist[v - 1];
        }
        self.by_vel.clear();
        self.by_vel.resize(list.len(), 0);
        for &i in list {
            let v = 127 - ((g[i].msg >> 16) & 0x7F) as usize;
            self.by_vel[hist[v] as usize] = i;
            hist[v] += 1;
        }
        // then grouped by track, stable (so loudest first inside each)
        self.tracks.clear();
        for &i in &self.by_vel {
            let t = g[i].track as usize;
            if self.per_track[t] == 0 {
                self.tracks.push(t as u16);
            }
            self.per_track[t] += 1;
        }
        let mut at = 0u32;
        for &t in &self.tracks {
            self.starts[t as usize] = at;
            at += self.per_track[t as usize];
        }
        self.sorted.clear();
        self.sorted.resize(list.len(), 0);
        for &i in &self.by_vel {
            let t = g[i].track as usize;
            self.sorted[self.starts[t] as usize] = i;
            self.starts[t] += 1;
        }
        // turns: tracks with the most notes first (then the higher track number: the render thread's equal
        // shares leave the remainder to those too), so each round is a prefix of the track list
        let mut tracks: Vec<(u32, u16, u32)> = self
            .tracks
            .iter()
            .map(|&t| (self.per_track[t as usize], t, self.starts[t as usize] - self.per_track[t as usize]))
            .collect();
        tracks.sort_unstable_by(|x, y| (y.0, y.1).cmp(&(x.0, x.1)));
        let mut active = tracks.len();
        let mut round = 0u32;
        while active > 0 {
            while active > 0 && tracks[active - 1].0 <= round {
                active -= 1;
            }
            for &(_, _, start) in &tracks[..active] {
                self.order.push(self.sorted[(start + round) as usize]);
            }
            round += 1;
        }
        for &t in &self.tracks {
            self.per_track[t as usize] = 0;
        }
    }
}
