//! "Lower it by itself": how many note-ons a block may take, learned from whether blocks keep up with real time,
//! and which ones go when there are more. FAIR BY TRACK (user 2026-10-27): every track with notes in the block
//! gets an equal share (a track needing less leaves the rest to the others); inside a track the quietest go
//! first. A dropped note-on's note-off is dropped too (counted per channel + key: kept notes take offs first).
//!
//! Block time alone misleads (measured 2026-10-27, RTX 3080 Ti): a lightly used graphics card slows itself down,
//! so easy blocks paced in real time take ~4 ms of 10.7 against ~1.2 ms back to back; a busy one runs at full
//! speed (Shinki's dense part: 29 ms either way). So "behind" = block time near real time OR the blocks kept
//! ready running out. Shinki before its wall: ~185,000 note-ons a block; even at 16,384 voices the note-ons
//! alone fill a block's time, so dropping new notes is what keeps real time.

use crate::Pending;

/// Block time as a share of its real-time length (running average): over HIGH = fewer notes, under LOW = more.
const HIGH: f64 = 0.9;
const LOW: f64 = 0.6;
/// Fewest note-ons a block may take while lowered (a melody stays audible).
const LEAST: f64 = 16.0;
/// Weight of the newest block in the running averages.
const SMOOTH: f64 = 0.25;
/// Blocks not judged at the start (the graphics card starting up; the first blocks are made before any notes
/// come, so few are needed).
const WARM: u64 = 4;

pub struct Budget {
    /// Note-ons a block may take; None = no limit.
    allow: Option<f64>,
    /// Note-ons the last block asked for.
    wanted: usize,
    /// Running averages: block time / real time, and how empty the ready blocks were (0 full .. 1 none).
    load: f64,
    empty: f64,
    blocks: u64,
    /// Per channel (0..256) x key: note-ons sounding that were kept, and dropped ones still waiting for an off.
    kept: Vec<u32>,
    gone: Vec<u32>,
    ons: Vec<usize>,
    keep: Vec<bool>,
    // (scratch for choose: counting sorts, no per-block allocation)
    per_track: Vec<u32>,
    tracks: Vec<u16>,
    by_track: Vec<usize>,
    starts: Vec<u32>,
}

impl Budget {
    pub fn new() -> Self {
        Budget {
            allow: None,
            wanted: 0,
            load: 0.0,
            empty: 0.0,
            blocks: 0,
            kept: vec![0; 256 * 128],
            gone: vec![0; 256 * 128],
            ons: Vec::new(),
            keep: Vec::new(),
            per_track: vec![0; 1 << 16],
            tracks: Vec::new(),
            by_track: Vec::new(),
            starts: vec![0; 1 << 16],
        }
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
                let k = ch * 128 + ((p.msg >> 8) & 0x7F) as usize;
                if is_on {
                    if self.keep[i] {
                        self.kept[k] += 1;
                    } else {
                        self.gone[k] += 1;
                    }
                } else if self.kept[k] > 0 {
                    // (a sounding kept note takes the off first: every kept note gets exactly one, none stuck;
                    // overlapping notes on one key may swap ends while notes are being dropped)
                    self.kept[k] -= 1;
                } else if self.gone[k] > 0 {
                    self.gone[k] -= 1;
                    continue; // (its note-on was dropped)
                }
            }
            if self.keep[i] {
                push(p);
            }
        }
        dropped
    }

    /// Mark all but `allow` note-ons as dropped: equal shares per track, loudest kept inside each (same
    /// velocity: the earlier one). Counting sorts only: a dense block has ~200,000 note-ons.
    fn choose(&mut self, block: &[Pending], allow: usize) {
        // note-ons grouped by track (stable: block order inside each)
        self.tracks.clear();
        for &i in &self.ons {
            let t = block[i].track as usize;
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
        self.by_track.clear();
        self.by_track.resize(self.ons.len(), 0);
        for &i in &self.ons {
            let t = block[i].track as usize;
            self.by_track[self.starts[t] as usize] = i;
            self.starts[t] += 1;
        }
        // shares: smallest tracks first, each takes min(its count, an equal share of what's left)
        let mut order: Vec<(u32, u16)> = self.tracks.iter().map(|&t| (self.per_track[t as usize], t)).collect();
        order.sort_unstable();
        let mut left = allow;
        let total = order.len();
        let mut take = vec![0u32; 0];
        take.resize(total, 0);
        for (n, &(count, _)) in order.iter().enumerate() {
            let share = left / (total - n);
            let k = (count as usize).min(share);
            take[n] = k as u32;
            left -= k;
        }
        // inside each track: keep its `take` loudest (velocity histogram, earlier first at the cut)
        for (n, &(count, t)) in order.iter().enumerate() {
            let k = take[n] as usize;
            let end = self.starts[t as usize] as usize; // (moved to the group's end by the fill above)
            let group = &self.by_track[end - count as usize..end];
            if k >= group.len() {
                continue;
            }
            let mut hist = [0u32; 128];
            for &i in group {
                hist[((block[i].msg >> 16) & 0x7F) as usize] += 1;
            }
            // lowest velocity `cut` kept, and how many at it
            let (mut cut, mut room) = (127usize, k);
            while cut > 0 && hist[cut] as usize <= room {
                room -= hist[cut] as usize;
                cut -= 1;
            }
            for &i in group {
                let v = ((block[i].msg >> 16) & 0x7F) as usize;
                if v < cut || (v == cut && {
                    let fits = room > 0;
                    room = room.saturating_sub(1);
                    !fits
                }) {
                    self.keep[i] = false;
                }
            }
        }
        for &t in &self.tracks {
            self.per_track[t as usize] = 0;
        }
    }

    /// A block took `load` of its real-time length to make, with `ready` of `ahead` blocks waiting when it
    /// started.
    pub fn measured(&mut self, load: f64, ready: usize, ahead: usize, lower: bool) {
        self.blocks += 1;
        // (one block is always being taken by the output: ahead - 1 ready = all well)
        let empty = 1.0 - (ready as f64 / (ahead.max(2) - 1) as f64).min(1.0);
        if self.blocks == 1 {
            (self.load, self.empty) = (load, empty);
        } else {
            self.load += (load - self.load) * SMOOTH;
            self.empty += (empty - self.empty) * SMOOTH;
        }
        if !lower {
            self.allow = None;
            return;
        }
        if self.blocks <= WARM {
            return;
        }
        if load > 1.0 || self.load > HIGH || self.empty > 0.5 {
            // (in proportion to how far behind this block was: a block 2.7x too slow cuts to ~30 % at once; a
            // fixed 0.7 took ~30 slow blocks to come down from Shinki's 185,000 a block = 0.7 s of silence)
            let base = self.allow.map_or(self.wanted as f64, |a| a.min(self.wanted as f64));
            let factor = (HIGH * 0.9 / load.max(1e-6)).min(0.7);
            self.allow = Some((base * factor).max(LEAST));
        } else if self.load < LOW && self.empty < 0.2 {
            if let Some(a) = self.allow {
                // back up while blocks keep up; no limit once it's far above what blocks ask for
                let next = a * 1.05 + 1.0;
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
    fn same_velocity_keeps_the_earlier() {
        let mut b = Budget::new();
        b.allow = Some(3.0);
        let mut block: Vec<Pending> = (0..6).map(|k| on(7, 40 + k, 64)).collect();
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p| kept.push((p.msg >> 8) & 0x7F));
        assert_eq!(kept, vec![40, 41, 42]);
        // (scratch left clean for the next block)
        let mut block: Vec<Pending> = (0..2).map(|k| on(7, 60 + k, 64)).collect();
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p| kept.push((p.msg >> 8) & 0x7F));
        assert_eq!(kept, vec![60, 61]);
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

    #[test]
    fn many_tracks_quickly() {
        // a Shinki-sized block: 200,000 note-ons over 5,000 tracks
        let mut b = Budget::new();
        b.allow = Some(20_000.0);
        let mut block: Vec<Pending> =
            (0..200_000u32).map(|i| on((i % 5000) as u16, i % 128, 1 + (i * 7) % 127)).collect();
        let t = std::time::Instant::now();
        let mut n = 0;
        let dropped = b.filter(&mut block, true, |_| n += 1);
        assert_eq!(dropped, 180_000);
        assert_eq!(n, 20_000);
        eprintln!("200,000 note-ons chosen in {:.2?}", t.elapsed());
        assert!(t.elapsed().as_millis() < 20);
    }
}
