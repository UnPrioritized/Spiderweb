//! "Lower it by itself": how many note-ons a block may take, learned from how long blocks take on the GPU, and
//! which ones go when there are more. FAIR BY TRACK (user 2026-10-27): every track with notes in the block gets
//! an equal share (a track needing less leaves the rest to the others); inside a track the quietest go first. A
//! dropped note-on's note-off is dropped too (paired in order per channel + key).

use std::collections::VecDeque;

use crate::Pending;

/// Block time as a share of its real-time length: over HIGH = fewer notes, under LOW = more again.
const HIGH: f64 = 0.6;
const LOW: f64 = 0.35;
/// Fewest note-ons a block may take while lowered (a melody stays audible).
const LEAST: f64 = 16.0;
/// Weight of the newest block in the running average of block load.
const SMOOTH: f64 = 0.25;
/// Blocks not judged at the start.
const WARM: u64 = 16;

pub struct Budget {
    /// Note-ons a block may take; None = no limit.
    allow: Option<f64>,
    /// Note-ons the last block asked for.
    wanted: usize,
    /// Running average of block time / real time, and blocks seen.
    load: f64,
    blocks: u64,
    /// Per channel (0..256) x key: for each sounding note-on, in order, whether it was dropped.
    held: Vec<VecDeque<bool>>,
    ons: Vec<usize>,
    order: Vec<(u16, u8, usize)>,
    keep: Vec<bool>,
}

impl Budget {
    pub fn new() -> Self {
        Budget { allow: None, wanted: 0, load: 0.0, blocks: 0, held: vec![VecDeque::new(); 256 * 128], ons: Vec::new(), order: Vec::new(), keep: Vec::new() }
    }

    pub fn allowance(&self) -> u32 {
        self.allow.map_or(u32::MAX, |a| a as u32)
    }

    /// Hand `push` the block's messages that stay; returns how many note-ons were dropped.
    pub fn filter(&mut self, block: &mut [Pending], lower: bool, mut push: impl FnMut(&Pending)) -> u64 {
        self.ons.clear();
        for (i, p) in block.iter().enumerate() {
            if p.msg & 0xF0 == 0x90 && (p.msg >> 16) & 0x7F != 0 {
                self.ons.push(i);
            }
        }
        self.wanted = self.ons.len();
        self.keep.clear();
        self.keep.resize(block.len(), true);
        let mut dropped = 0u64;
        if let Some(allow) = self.allow.filter(|_| lower) {
            let allow = allow as usize;
            if self.ons.len() > allow {
                dropped = (self.ons.len() - allow) as u64;
                self.choose(block, allow);
            }
        }
        for (i, p) in block.iter().enumerate() {
            let status = p.msg & 0xF0;
            let is_on = status == 0x90 && (p.msg >> 16) & 0x7F != 0;
            let is_off = status == 0x80 || (status == 0x90 && !is_on);
            if is_on || is_off {
                let ch = ((((p.msg >> 24) & 0x0F) << 4) | (p.msg & 0x0F)) as usize;
                let slot = &mut self.held[ch * 128 + ((p.msg >> 8) & 0x7F) as usize];
                if is_on {
                    slot.push_back(!self.keep[i]);
                } else if slot.pop_front() == Some(true) {
                    continue; // (its note-on was dropped)
                }
            }
            if self.keep[i] {
                push(p);
            }
        }
        dropped
    }

    /// Mark all but `allow` note-ons as dropped: equal shares per track, loudest kept inside each.
    fn choose(&mut self, block: &[Pending], allow: usize) {
        // (track, velocity, index), grouped by track, loudest first inside it
        self.order.clear();
        self.order.extend(self.ons.iter().map(|&i| (block[i].track, ((block[i].msg >> 16) & 0x7F) as u8, i)));
        self.order.sort_unstable_by(|a, b| a.0.cmp(&b.0).then(b.1.cmp(&a.1)).then(a.2.cmp(&b.2)));
        let mut groups: Vec<(usize, usize)> = Vec::new(); // (start, len) in order
        for (n, o) in self.order.iter().enumerate() {
            match groups.last_mut() {
                Some(g) if self.order[g.0].0 == o.0 => g.1 += 1,
                _ => groups.push((n, 1)),
            }
        }
        // smallest tracks first: each takes min(its count, an equal share of what's left)
        groups.sort_unstable_by_key(|g| g.1);
        let mut left = allow;
        let total = groups.len();
        for (n, &(start, len)) in groups.iter().enumerate() {
            let share = left / (total - n);
            let take = len.min(share);
            left -= take;
            for o in &self.order[start + take..start + len] {
                self.keep[o.2] = false;
            }
        }
    }

    /// A block took `load` of its real-time length to make. Judged on a running average (one slow block is
    /// covered by the blocks kept ready), and not in the first WARM blocks (the GPU starting up).
    pub fn measured(&mut self, load: f64, lower: bool) {
        self.blocks += 1;
        self.load = if self.blocks == 1 { load } else { self.load + (load - self.load) * SMOOTH };
        if !lower {
            self.allow = None;
            return;
        }
        if self.blocks <= WARM {
            return;
        }
        let load = self.load;
        if load > HIGH {
            let base = self.allow.map_or(self.wanted as f64, |a| a.min(self.wanted as f64));
            self.allow = Some((base * 0.7).max(LEAST));
        } else if load < LOW {
            if let Some(a) = self.allow {
                // back up while blocks are quick; no limit once it's far above what blocks ask for
                let next = a * 1.1 + 1.0;
                self.allow = if next > (self.wanted.max(1) as f64) * 4.0 { None } else { Some(next) };
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn on(track: u16, key: u32, vel: u32) -> Pending {
        Pending { frame: 0, msg: 0x90 | key << 8 | vel << 16, track }
    }

    fn off(key: u32) -> Pending {
        Pending { frame: 0, msg: 0x80 | key << 8, track: 0 }
    }

    #[test]
    fn fair_shares_and_loudest_kept() {
        let mut b = Budget::new();
        b.allow = Some(4.0);
        // track 1: a wall of 10 quiet notes; track 2: two loud melody notes
        let mut block: Vec<Pending> = (0..10).map(|k| on(1, 40 + k, 10 + k)).collect();
        block.push(on(2, 80, 100));
        block.push(on(2, 81, 90));
        let mut kept = Vec::new();
        let dropped = b.filter(&mut block, true, |p| kept.push(p.msg));
        assert_eq!(dropped, 8);
        // the melody's two notes + the wall's two loudest
        let keys: Vec<u32> = kept.iter().map(|m| (m >> 8) & 0x7F).collect();
        assert_eq!(keys, vec![48, 49, 80, 81]);
    }

    #[test]
    fn a_dropped_note_loses_its_off() {
        let mut b = Budget::new();
        b.allow = Some(1.0);
        let mut block = vec![on(1, 60, 100), on(1, 60, 5)];
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p| kept.push(p.msg));
        assert_eq!(kept.len(), 1);
        // first off pairs with the kept (first) note-on, the second with the dropped one
        let mut block = vec![off(60), off(60)];
        let mut kept = Vec::new();
        b.allow = None;
        b.filter(&mut block, true, |p| kept.push(p.msg));
        assert_eq!(kept.len(), 1);
    }

    #[test]
    fn off_means_everything_plays() {
        let mut b = Budget::new();
        b.allow = Some(1.0);
        let mut block: Vec<Pending> = (0..5).map(|k| on(1, 40 + k, 50)).collect();
        let mut n = 0;
        assert_eq!(b.filter(&mut block, false, |_| n += 1), 0);
        assert_eq!(n, 5);
    }
}
