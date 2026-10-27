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

/// `Pending::group` of an event that wasn't ranked (not a note-on, or its block wasn't busy enough).
pub const NO_GROUP: u64 = 0;
/// Note-ons in a block past which it is ranked whatever the allowance is now (a wall arriving while there is no
/// limit yet must be ranked when the first cut comes).
pub const RANK_ALWAYS: usize = 20_000;
/// Frames of a note counted when a dropped copy's loudness is shared by length (~2 s at 48 kHz: a piano note
/// has faded by then; a note whose end isn't known counts this long).
pub const HEARD: u32 = 96_000;

/// Frames a note keeps sounding after its note-off (its release): a 2 ms spam note still makes ~0.5 s of sound.
/// Counted by note length alone, spam copies merged into a long note added almost nothing (Shinki: merging on
/// or off measured the same, 2026-10-27).
pub static TAIL: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(24_000);

/// A copy's sounding time (length + TAIL) against the note it is merged into (both capped at HEARD + TAIL): 1 =
/// as long.
pub fn len_ratio(copy: u32, into: u32) -> f32 {
    let tail = TAIL.load(std::sync::atomic::Ordering::Relaxed);
    let (c, t) = (copy.min(HEARD) + tail, into.min(HEARD) + tail);
    (c.max(1) as f32 / t.max(1) as f32).min(1.0)
}

/// MERGED BY TIME (user 2026-10-27, after the low keys came out 4-6 dB too loud): all of a stack's loudness on
/// its longest note for that note's whole length gave long-ringing low notes far too much. A copy adds as an
/// amplitude only on the same frame, at the same velocity AND about as long (`COHERENT`), else as power times
/// its share of the time.
pub const COHERENT: f32 = 0.9;

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

    /// Rank the note-ons of every block in `batch` with more than `over` of them (frames in a row of one block =
    /// one group; the others stay unranked: chosen on the render thread if they ever need choosing).
    pub fn rank(&mut self, batch: &mut [Pending], block_frames: u64, over: usize) {
        let mut a = 0;
        while a < batch.len() {
            let block = batch[a].frame / block_frames;
            let mut b = a + 1;
            while b < batch.len() && batch[b].frame / block_frames == block {
                b += 1;
            }
            let ons = batch[a..b].iter().filter(|p| p.msg & 0xF0 == 0x90 && (p.msg >> 16) & 0x7F != 0).count();
            if ons > over {
                self.serial += 1;
                self.rank_group(&mut batch[a..b], self.serial);
            }
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
            // (the same sound = same frame AND same velocity: other velocities may be other samples, which add
            // as powers; adding all same-frame copies as amplitudes left Shinki's F#2 / G2 6 dB too loud against
            // every note played with 4 M voices, 2026-10-27)
            let vel = |p: &Pending| (p.msg >> 16) & 0x7F;
            g[i].s_same = g[s].frame == g[i].frame && vel(&g[s]) == vel(&g[i]);
            g[i].k_same = g[k].frame == g[i].frame && vel(&g[k]) == vel(&g[i]);
            g[i].s_ratio = len_ratio(g[i].len, g[s].len);
            g[i].k_ratio = len_ratio(g[i].len, g[k].len);
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
