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

/// Block time as a share of its real-time length (running average): over HIGH = fewer notes, under TARGET =
/// more again.
const HIGH: f64 = 0.9;
const TARGET: f64 = 0.7;
/// Fewest note-ons a block may take while lowered (128 = one start of every key always fits: tier 1), and
/// fewest voices.
const LEAST: f64 = 128.0;
/// (voices cost little under ~65k: Shinki's dense part 10.7 ms a block at 16k, 11.9 at 65k, 29 at 1M; a cap of
/// ~4,000 stole every ringing note: the held lines in the user's reference render were gone, 2026-10-27)
const LEAST_VOICES: f64 = 65536.0;
/// Most a block raises the limits by, with all its time to spare.
const CLIMB: f64 = 0.08;
/// Weight of the newest block in the running averages.
const SMOOTH: f64 = 0.25;
/// Blocks with sound not judged at the start: the first one sounding does the graphics card's one-time setup
/// and took ~10x a normal block (Shinki: judged, its one cut took 185,000 note-ons a block down to 39).
const WARM: u64 = 1;

pub struct Budget {
    /// Note-ons a block may take; None = no limit.
    allow: Option<f64>,
    /// Note-ons the last block asked for.
    wanted: usize,
    /// Running averages: block time / real time, and how empty the ready blocks were (0 full .. 1 none).
    load: f64,
    empty: f64,
    blocks: u64,
    /// Note-ons a block may take that last proved too many (climbed past slowly), and blocks left before
    /// another cut may come.
    ceiling: Option<f64>,
    cooldown: u32,
    /// The limit was raised since the last cut; the averages start over after the cut's wait.
    raised: bool,
    fresh: bool,
    /// Cuts so far.
    pub cuts: u32,
    ahead: usize,
    /// Voices Kestrel may sound now (None = the user's Max voices), and that Max.
    cap: Option<f64>,
    max_voices: u32,
    /// Per channel (0..256) x key: note-ons sounding that were kept, and dropped ones still waiting for an off.
    kept: Vec<u32>,
    gone: Vec<u32>,
    ons: Vec<usize>,
    keep: Vec<bool>,
    /// Different keys that wanted to start / still started, summed over blocks (runs lost show here).
    pub keys_wanted: u64,
    pub keys_kept: u64,
    // (scratch for tiers / choose: counting sorts, no per-block allocation)
    by_slot: Vec<usize>,
    coh: Vec<f64>,
    pow: Vec<f64>,
    /// Loudness factor of a kept note carrying merged copies (1 = as sent).
    scale: Vec<f32>,
    t1: Vec<usize>,
    t2: Vec<usize>,
    t3: Vec<usize>,
    per_track: Vec<u32>,
    tracks: Vec<u16>,
    by_track: Vec<usize>,
    starts: Vec<u32>,
    by_rank: Vec<usize>,
    /// Blocks chosen from the push-time order (`ranked`), not by `tiers`.
    pub fast: u64,
}

impl Budget {
    pub fn new(max_voices: u32) -> Self {
        Budget {
            allow: None,
            wanted: 0,
            load: 0.0,
            empty: 0.0,
            blocks: 0,
            ceiling: None,
            cooldown: 0,
            raised: true,
            fresh: false,
            cuts: 0,
            ahead: 2,
            cap: None,
            max_voices,
            kept: vec![0; 256 * 128],
            gone: vec![0; 256 * 128],
            ons: Vec::new(),
            keep: Vec::new(),
            keys_wanted: 0,
            keys_kept: 0,
            by_slot: vec![usize::MAX; 256 * 128],
            coh: Vec::new(),
            pow: Vec::new(),
            scale: Vec::new(),
            t1: Vec::new(),
            t2: Vec::new(),
            t3: Vec::new(),
            per_track: vec![0; 1 << 16],
            tracks: Vec::new(),
            by_track: Vec::new(),
            starts: vec![0; 1 << 16],
            by_rank: Vec::new(),
            fast: 0,
        }
    }

    pub fn allowance(&self) -> u32 {
        self.allow.map_or(u32::MAX, |a| a as u32)
    }

    /// Voices Kestrel may sound now (the user's Max voices when not lowered).
    pub fn voice_cap(&self) -> u32 {
        self.cap.map_or(self.max_voices, |c| c as u32)
    }

    /// Hand `push` the block's messages that stay, each with its loudness factor (amplitude: copies merged into
    /// a note-on); returns how many note-ons were dropped.
    pub fn filter(&mut self, block: &[Pending], lower: bool, mut push: impl FnMut(&Pending, f32)) -> u64 {
        // one ranked group over the allowance: the lean way (no arrays the size of the block: a Shinki block of
        // ~370,000 messages took 1.75 ms the general way, mostly zeroing them)
        if let Some(allow) = self.allow.filter(|_| lower) {
            if let Some(d) = self.filter_ranked(block, allow as usize, &mut push) {
                return d;
            }
        }
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
                if !self.ranked(block, allow) {
                    self.tiers(block, allow);
                }
            }
        }
        // (how many different keys wanted to start, and how many still do: runs lost show here)
        let mut want_keys = [false; 128];
        let mut kept_keys = [false; 128];
        for &i in &self.ons {
            let key = ((block[i].msg >> 8) & 0x7F) as usize;
            want_keys[key] = true;
            kept_keys[key] |= self.keep[i];
        }
        self.keys_wanted += want_keys.iter().filter(|&&k| k).count() as u64;
        self.keys_kept += kept_keys.iter().filter(|&&k| k).count() as u64;
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
                // (a kept note-on carries the loudness of the copies merged into it)
                push(p, if is_on { self.scale.get(i).copied().unwrap_or(1.0) } else { 1.0 });
            }
        }
        self.scale.clear();
        dropped
    }

    /// `filter` for a block whose note-ons were all ranked together at push and are more than `allow`: kept =
    /// rank < allow, the merged loudness kept in arrays the size of `allow`. None = not such a block.
    fn filter_ranked(&mut self, block: &[Pending], allow: usize, push: &mut impl FnMut(&Pending, f32)) -> Option<u64> {
        let is_on = |p: &Pending| p.msg & 0xF0 == 0x90 && (p.msg >> 16) & 0x7F != 0;
        let amp = |p: &Pending| {
            let v = ((p.msg >> 16) & 0x7F) as f64 / 127.0;
            v * v
        };
        // pass 1: one group with ranks 0..n; each dropped note's loudness to the same channel + key's kept note,
        // else the key's tier-1 note (always kept), as an amplitude when on its frame (known from push)
        self.coh.clear();
        self.coh.resize(allow, 0.0);
        self.pow.clear();
        self.pow.resize(allow, 0.0);
        let (mut n, mut group, mut top) = (0usize, crate::rank::NO_GROUP, 0u32);
        for p in block {
            if !is_on(p) {
                continue;
            }
            if n == 0 {
                group = p.group;
            } else if p.group != group {
                return None;
            }
            n += 1;
            top = top.max(p.rank);
            if p.rank as usize >= allow {
                let (to, same) = if (p.srep as usize) < allow { (p.srep, p.s_same) } else { (p.krep, p.k_same) };
                let to = to as usize;
                if to >= allow {
                    return None; // (can't be: tier 1 always fits; the general way then)
                }
                if same {
                    self.coh[to] += amp(p);
                } else {
                    self.pow[to] += amp(p) * amp(p);
                }
            }
        }
        if n <= allow || group == crate::rank::NO_GROUP || top as usize >= n {
            return None;
        }
        self.fast += 1;
        self.wanted = n;
        // pass 2: send, offs of dropped notes dropped (counted per channel + key), keys counted
        let mut want_keys = [false; 128];
        let mut kept_keys = [false; 128];
        for p in block {
            let status = p.msg & 0xF0;
            let on = is_on(p);
            let off = status == 0x80 || (status == 0x90 && !on);
            if !on && !off {
                push(p, 1.0);
                continue;
            }
            let ch = ((((p.msg >> 24) & 0x0F) << 4) | (p.msg & 0x0F)) as usize;
            let key = ((p.msg >> 8) & 0x7F) as usize;
            let k = ch * 128 + key;
            if on {
                let r = p.rank as usize;
                want_keys[key] = true;
                if r < allow {
                    kept_keys[key] = true;
                    self.kept[k] += 1;
                    let (c, w) = (self.coh[r], self.pow[r]);
                    let scale = if c > 0.0 || w > 0.0 {
                        let a = amp(p);
                        (((a + c).powi(2) + w).sqrt() / a) as f32
                    } else {
                        1.0
                    };
                    push(p, scale);
                } else {
                    self.gone[k] += 1;
                }
            } else if self.kept[k] > 0 {
                self.kept[k] -= 1;
                push(p, 1.0);
            } else if self.gone[k] > 0 {
                self.gone[k] -= 1;
            } else {
                push(p, 1.0);
            }
        }
        self.keys_wanted += want_keys.iter().filter(|&&k| k).count() as u64;
        self.keys_kept += kept_keys.iter().filter(|&&k| k).count() as u64;
        Some((n - allow) as u64)
    }

    /// The block's note-ons all ranked together at push (rank.rs): keep the first `allow` of that order and merge
    /// the rest into their kept notes, with no sorting here. False = not one ranked group (pushed in two parts,
    /// or late into this block): `tiers` chooses instead.
    fn ranked(&mut self, block: &[Pending], allow: usize) -> bool {
        let n = self.ons.len();
        let group = block[self.ons[0]].group;
        if group == crate::rank::NO_GROUP
            || self.ons.iter().any(|&i| block[i].group != group || block[i].rank as usize >= n)
        {
            return false;
        }
        self.by_rank.clear();
        self.by_rank.resize(n, usize::MAX);
        for &i in &self.ons {
            self.by_rank[block[i].rank as usize] = i;
        }
        if self.by_rank.contains(&usize::MAX) {
            return false;
        }
        self.fast += 1;
        let amp = |i: usize| {
            let v = ((block[i].msg >> 16) & 0x7F) as f64 / 127.0;
            v * v
        };
        self.coh.clear();
        self.coh.resize(block.len(), 0.0);
        self.pow.clear();
        self.pow.resize(block.len(), 0.0);
        for &i in &self.ons {
            let p = &block[i];
            if (p.rank as usize) < allow {
                continue;
            }
            self.keep[i] = false;
            // (its loudness to the same channel + key's kept note, else the key's tier-1 note: always kept)
            let to = if (p.srep as usize) < allow { p.srep } else { p.krep };
            let t = self.by_rank[to as usize];
            if p.frame == block[t].frame {
                self.coh[t] += amp(i);
            } else {
                self.pow[t] += amp(i) * amp(i);
            }
        }
        self.scale.clear();
        self.scale.resize(block.len(), 1.0);
        for &i in &self.ons {
            if self.keep[i] && (self.coh[i] > 0.0 || self.pow[i] > 0.0) {
                let a = ((amp(i) + self.coh[i]).powi(2) + self.pow[i]).sqrt();
                self.scale[i] = (a / amp(i)) as f32;
            }
        }
        true
    }

    /// Keep `allow` of the block's note-ons, in tiers (user 2026-10-27: thinned by loudness alone, the runs
    /// running up and down were gone: walls are mostly STACKED COPIES of one key): 1. the loudest start of each
    /// key (any channel; at most 128, so every pitch keeps sounding), 2. the loudest of each key on each
    /// channel (other instruments on that key), 3. the rest. A tier that doesn't fit whole is shared fairly
    /// by track (`choose`); the tiers after it are dropped.
    fn tiers(&mut self, block: &[Pending], allow: usize) {
        let key = |i: usize| ((block[i].msg >> 8) & 0x7F) as usize;
        let slot = |i: usize| ((((block[i].msg >> 24) & 0x0F) << 4 | (block[i].msg & 0x0F)) as usize) * 128 + key(i);
        // the start kept for a key: the LONGEST, then the loudest (user 2026-10-27: a held note beside spam on one
        // key lost to the louder spam: its line was gone from the spectrogram)
        let rank = |i: usize| (block[i].len, (block[i].msg >> 16) & 0x7F);
        let mut by_key = [usize::MAX; 128];
        for &i in &self.ons {
            let b = &mut by_key[key(i)];
            if *b == usize::MAX || rank(i) > rank(*b) {
                *b = i;
            }
        }
        for &i in &self.ons {
            let b = &mut self.by_slot[slot(i)];
            if *b == usize::MAX || rank(i) > rank(*b) {
                *b = i;
            }
        }
        let (mut t1, mut t2, mut t3) = (std::mem::take(&mut self.t1), std::mem::take(&mut self.t2), std::mem::take(&mut self.t3));
        t1.clear();
        t2.clear();
        t3.clear();
        for &i in &self.ons {
            if by_key[key(i)] == i {
                t1.push(i);
            } else if self.by_slot[slot(i)] == i {
                t2.push(i);
            } else {
                t3.push(i);
            }
        }
        for &i in &self.ons {
            self.by_slot[slot(i)] = usize::MAX;
        }
        let mut left = allow;
        for tier in [&t1, &t2, &t3] {
            if tier.len() <= left {
                left -= tier.len();
            } else {
                self.choose(block, tier, left);
                left = 0;
            }
        }
        (self.t1, self.t2, self.t3) = (t1, t2, t3);
        self.merge_loudness(block);
    }

    /// MERGED, NOT JUST DROPPED (user 2026-10-27): each dropped copy's loudness goes to a kept note of its key
    /// (same channel if there is one, else the key's tier-1 note), which is played louder: a key stacked many
    /// times stays louder than its neighbours, as in a full render. Kestrel's velocity curve at full tracking:
    /// amplitude = (vel / 127)^2 (bank.rs velocity_atten_cb). Copies on the same frame add as amplitudes (the
    /// same sound starting together), on other frames as powers.
    fn merge_loudness(&mut self, block: &[Pending]) {
        let amp = |i: usize| {
            let v = ((block[i].msg >> 16) & 0x7F) as f64 / 127.0;
            v * v
        };
        let key = |i: usize| ((block[i].msg >> 8) & 0x7F) as usize;
        let slot = |i: usize| ((((block[i].msg >> 24) & 0x0F) << 4 | (block[i].msg & 0x0F)) as usize) * 128 + key(i);
        // (the longest kept note of each channel + key, and of each key, takes the loudness)
        let rank = |i: usize| (block[i].len, (block[i].msg >> 16) & 0x7F);
        let mut by_key = [usize::MAX; 128];
        for &i in &self.ons {
            if self.keep[i] {
                let s = &mut self.by_slot[slot(i)];
                if *s == usize::MAX || rank(i) > rank(*s) {
                    *s = i;
                }
                let k = &mut by_key[key(i)];
                if *k == usize::MAX || rank(i) > rank(*k) {
                    *k = i;
                }
            }
        }
        // per kept note: amplitude on its own frame, power from other frames
        self.coh.clear();
        self.coh.resize(block.len(), 0.0);
        self.pow.clear();
        self.pow.resize(block.len(), 0.0);
        let mut any = false;
        for &i in &self.ons {
            if self.keep[i] {
                continue;
            }
            let s = self.by_slot[slot(i)];
            let t = if s != usize::MAX { s } else { by_key[key(i)] };
            if t == usize::MAX {
                continue;
            }
            any = true;
            if block[i].frame == block[t].frame {
                self.coh[t] += amp(i);
            } else {
                self.pow[t] += amp(i) * amp(i);
            }
        }
        self.scale.clear();
        self.scale.resize(block.len(), 1.0);
        if any {
            for &i in &self.ons {
                if self.keep[i] && (self.coh[i] > 0.0 || self.pow[i] > 0.0) {
                    // (FULL merging, user 2026-10-27: our Kestrel change push_scaled multiplies the voice's gain,
                    // no velocity-127 ceiling and the note's own velocity layer; the quick version's raised
                    // velocity left the most stacked keys 14-19 dB short)
                    let a = ((amp(i) + self.coh[i]).powi(2) + self.pow[i]).sqrt();
                    self.scale[i] = (a / amp(i)) as f32;
                }
            }
        }
        for &i in &self.ons {
            self.by_slot[slot(i)] = usize::MAX;
        }
    }

    /// Mark all but `allow` of `cands` as dropped: equal shares per track, loudest kept inside each (same
    /// velocity: the earlier one). Counting sorts only: a dense block has ~200,000 note-ons.
    fn choose(&mut self, block: &[Pending], cands: &[usize], allow: usize) {
        if allow == 0 {
            for &i in cands {
                self.keep[i] = false;
            }
            return;
        }
        // note-ons grouped by track (stable: block order inside each)
        self.tracks.clear();
        for &i in cands {
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
        self.by_track.resize(cands.len(), 0);
        for &i in cands {
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
    pub fn measured(&mut self, load: f64, ready: usize, ahead: usize, voices: u64, lower: bool) {
        self.ahead = ahead;
        if self.blocks == 0 && self.wanted == 0 && voices == 0 {
            return; // (silence before the first note: nothing to learn)
        }
        self.blocks += 1;
        // (one block is always being taken by the output: ahead - 1 ready = all well)
        let empty = 1.0 - (ready as f64 / (ahead.max(2) - 1) as f64).min(1.0);
        if self.blocks == 1 || (self.fresh && self.cooldown == 0) {
            (self.load, self.empty) = (load, empty);
            self.fresh = false;
        } else {
            self.load += (load - self.load) * SMOOTH;
            self.empty += (empty - self.empty) * SMOOTH;
        }
        if !lower {
            self.allow = None;
            self.cap = None;
            return;
        }
        if self.blocks <= WARM {
            return;
        }
        if self.cooldown > 0 {
            self.cooldown -= 1;
            return;
        }
        if load > 1.0 || self.load > HIGH || self.empty > 0.5 {
            // (in proportion to how far behind this block was: a block 2.7x too slow cuts to ~30 % at once; a
            // fixed 0.7 took ~30 slow blocks to come down from Shinki's 185,000 a block = 0.7 s of silence)
            let base = self.allow.map_or(self.wanted as f64, |a| a.min(self.wanted as f64));
            let factor = (HIGH * 0.9 / load.max(1e-6)).min(0.7);
            // the level that was too much: climbed past only slowly from now on (it swung every ~75 ms
            // otherwise, each swing a 5-9 ms gap: user heard it, 2026-10-27). Only a level that was CLIMBED
            // to counts: cuts in a row for one problem pushed it down to nothing.
            if self.raised {
                self.ceiling = Some(base * 0.9);
            }
            self.raised = false;
            self.allow = Some((base * factor).max(LEAST));
            // and the voices sounding: notes already ringing kept blocks slow after every new note was dropped
            // (Shinki: ~1 M voices from the first blocks); Kestrel steals the quietest down to the cap
            let sounding = self.cap.map_or(voices as f64, |c| c.min(voices as f64));
            self.cap = Some((sounding * factor).max(LEAST_VOICES));
            // notes already started keep blocks slow for a while (a piano note rings on): let the cut show
            // before judging again (= the blocks kept ready), and judge only blocks made after it (the average
            // still held the slow blocks: three cuts in a row took Shinki from 185,000 note-ons a block to 36)
            self.cooldown = self.ahead as u32;
            self.fresh = true;
            self.cuts += 1;
        } else if self.load < TARGET && self.empty < 0.2 {
            if let Some(a) = self.allow {
                // in proportion to the time to spare: up to 8 % a block far below TARGET (~2x a quarter
                // second; 3 % climbed ~2x a second), slower near it; past the last trouble level a tenth of that
                let spare = (TARGET - self.load) / TARGET;
                let step = CLIMB * spare * if self.ceiling.is_some_and(|c| a >= c) { 0.1 } else { 1.0 };
                let next = a * (1.0 + step) + 1.0;
                self.raised = true;
                // no limit once it's far above what blocks ask for
                self.allow = if next > (self.wanted.max(1) as f64) * 4.0 { None } else { Some(next) };
            }
            if let Some(c) = self.cap {
                // the voice cap back up the same way, up to the user's Max voices
                let spare = (TARGET - self.load) / TARGET;
                let next = c * (1.0 + CLIMB * spare) + 16.0;
                self.cap = if next >= self.max_voices as f64 { None } else { Some(next) };
            }
            // the old trouble level is forgotten slowly (a lighter part may follow)
            if let Some(c) = self.ceiling.as_mut() {
                *c *= 1.0005;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn on(track: u16, key: u32, vel: u32) -> Pending {
        Pending::new(0, 0x90 | key << 8 | vel << 16, track)
    }

    fn off(key: u32) -> Pending {
        Pending::new(0, 0x80 | key << 8, 0)
    }

    /// The same blocks chosen from the push-time order give what the render-thread choice gives (both ways,
    /// every allowance), merged loudness included.
    #[test]
    fn ranked_at_push_chooses_the_same() {
        let mut seed = 7u64;
        let mut rnd = |n: u32| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed % n as u64) as u32
        };
        for case in 0..40 {
            let n = 50 + rnd(3000) as usize;
            let mut block: Vec<Pending> = (0..n)
                .map(|_| {
                    let mut p = on(rnd(9) as u16, 30 + rnd(24), 1 + rnd(127));
                    p.msg |= rnd(3) << 24 | rnd(2);
                    p.frame = rnd(4) as u64;
                    p.len = if rnd(3) == 0 { crate::LONG } else { rnd(5000) };
                    p
                })
                .collect();
            block.sort_by_key(|p| p.frame);
            let mut ranked = block.clone();
            crate::rank::Ranker::new().rank(&mut ranked, 512);
            for allow in [128usize, 200, 700, n.saturating_sub(1)] {
                if allow >= n {
                    continue; // (nothing to drop: neither way runs)
                }
                let (mut a, mut b) = (Budget::new(1 << 20), Budget::new(1 << 20));
                a.allow = Some(allow as f64);
                b.allow = Some(allow as f64);
                let (mut x, mut y) = (Vec::new(), Vec::new());
                a.filter(&mut block.clone(), true, |p, s| x.push((p.msg, p.track, p.frame, (s * 1e4).round() as i64)));
                b.filter(&mut ranked.clone(), true, |p, s| y.push((p.msg, p.track, p.frame, (s * 1e4).round() as i64)));
                assert!(b.fast > 0, "case {case}: the ranked block wasn't taken the fast way");
                x.sort();
                y.sort();
                assert_eq!(x.len(), y.len(), "case {case} allow {allow}");
                // (which copy of a velocity is kept may differ: counts per channel + key + velocity + track agree,
                // and every key's merged loudness)
                let tally = |v: &[(u32, u16, u64, i64)]| {
                    let mut t: Vec<(u32, u16)> = v.iter().map(|e| (e.0, e.1)).collect();
                    t.sort();
                    t
                };
                assert_eq!(tally(&x), tally(&y), "case {case} allow {allow}");
                let loud = |v: &[(u32, u16, u64, i64)]| {
                    let mut per = std::collections::BTreeMap::new();
                    for e in v {
                        let a = ((e.0 >> 16) & 0x7F) as f64 / 127.0;
                        *per.entry((e.0 >> 8) & 0x7F).or_insert(0.0) += a * a * e.3 as f64 / 1e4;
                    }
                    per.into_iter().map(|(k, v)| (k, (v * 1e3).round() as i64)).collect::<Vec<_>>()
                };
                assert_eq!(loud(&x), loud(&y), "case {case} allow {allow}: merged loudness per key");
            }
        }
    }

    #[test]
    fn a_held_note_beats_louder_spam_on_its_key() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(128.0);
        // key 60: 500 loud spam notes 100 frames long, and one quiet held note
        let mut block: Vec<Pending> = (0..500).map(|_| Pending { len: 100, ..on(1, 60, 120) }).collect();
        block.push(on(2, 60, 40));
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p, s| kept.push((p.track, (p.msg >> 16) & 0x7F, s)));
        // the held note is kept (tier 1) and carries the spam's loudness: 500 x (120/127)^2 / (40/127)^2 + 1
        let held = kept.iter().find(|k| k.0 == 2).expect("the held note was dropped");
        assert_eq!(held.1, 40);
        assert!(held.2 > 1000.0, "{held:?}");
    }

    #[test]
    fn fair_shares_and_loudest_kept() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(4.0);
        // track 1: a wall of 10 quiet notes; track 2: two loud melody notes
        let mut block: Vec<Pending> = (0..10).map(|k| on(1, 40 + k, 10 + k)).collect();
        block.push(on(2, 80, 100));
        block.push(on(2, 81, 90));
        let mut kept = Vec::new();
        let dropped = b.filter(&mut block, true, |p, _| kept.push(p.msg));
        assert_eq!(dropped, 8);
        // the melody's two notes + the wall's two loudest
        let keys: Vec<u32> = kept.iter().map(|m| (m >> 8) & 0x7F).collect();
        assert_eq!(keys, vec![48, 49, 80, 81]);
    }

    #[test]
    fn a_quiet_run_survives_a_loud_stack() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(128.0);
        // track 1: 1,000 loud copies of key 60; track 2: a quiet run note on key 72; track 3: a copy of 72
        let mut block: Vec<Pending> = (0..1000).map(|_| on(1, 60, 127)).collect();
        block.push(on(2, 72, 30));
        block.push(on(3, 72, 20));
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p, _| kept.push(((p.msg >> 8) & 0x7F, (p.msg >> 16) & 0x7F)));
        assert_eq!(kept.len(), 128);
        assert!(kept.contains(&(72, 30)), "the run note was dropped");
        assert!(!kept.contains(&(72, 20)) || kept.iter().filter(|k| k.0 == 72).count() == 2);
        assert_eq!((b.keys_wanted, b.keys_kept), (2, 2));
    }

    #[test]
    fn dropped_copies_make_the_kept_one_louder() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(128.0);
        // 129 different keys can't happen (128 keys), so: 128 keys once + a second copy of key 0, same frame
        let mut block: Vec<Pending> = (0..128).map(|k| on(1, k, 50)).collect();
        block.push(on(1, 0, 50));
        let mut kept = Vec::new();
        assert_eq!(b.filter(&mut block, true, |p, s| kept.push(((p.msg >> 8) & 0x7F, (p.msg >> 16) & 0x7F, s))), 1);
        // two copies on one frame = the kept one at its own velocity, twice the amplitude
        assert!(kept.contains(&(0, 50, 2.0)), "{:?}", &kept[..3]);
        assert!(kept.contains(&(1, 50, 1.0)));
        // on different frames they add as powers: sqrt(2)
        let mut block: Vec<Pending> = (0..128).map(|k| on(1, k, 50)).collect();
        block.push(Pending { frame: 7, ..on(1, 0, 50) });
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p, s| kept.push(((p.msg >> 8) & 0x7F, s)));
        let s = kept.iter().find(|k| k.0 == 0).unwrap().1;
        assert!((s - std::f32::consts::SQRT_2).abs() < 1e-5, "{s}");
    }

    #[test]
    fn same_velocity_keeps_the_earlier() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(3.0);
        let mut block: Vec<Pending> = (0..6).map(|k| on(7, 40 + k, 64)).collect();
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p, _| kept.push((p.msg >> 8) & 0x7F));
        assert_eq!(kept, vec![40, 41, 42]);
        // (scratch left clean for the next block)
        let mut block: Vec<Pending> = (0..2).map(|k| on(7, 60 + k, 64)).collect();
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p, _| kept.push((p.msg >> 8) & 0x7F));
        assert_eq!(kept, vec![60, 61]);
    }

    #[test]
    fn a_dropped_note_loses_its_off() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(1.0);
        let mut block = vec![on(1, 60, 100), on(1, 60, 5)];
        let mut kept = Vec::new();
        b.filter(&mut block, true, |p, _| kept.push(p.msg));
        assert_eq!(kept.len(), 1);
        // first off pairs with the kept (first) note-on, the second with the dropped one
        let mut block = vec![off(60), off(60)];
        let mut kept = Vec::new();
        b.allow = None;
        b.filter(&mut block, true, |p, _| kept.push(p.msg));
        assert_eq!(kept.len(), 1);
    }

    #[test]
    fn off_means_everything_plays() {
        let mut b = Budget::new(1 << 20);
        b.allow = Some(1.0);
        let mut block: Vec<Pending> = (0..5).map(|k| on(1, 40 + k, 50)).collect();
        let mut n = 0;
        assert_eq!(b.filter(&mut block, false, |_, _| n += 1), 0);
        assert_eq!(n, 5);
    }

    /// Measures (ignored): one Shinki-sized ranked block through `filter`, by part.
    #[test]
    #[ignore = "measures"]
    fn shinki_block_time() {
        let mut block = Vec::new();
        for i in 0..185_000u32 {
            let mut p = on((i % 3000) as u16, 20 + i % 88, 1 + (i * 7) % 127);
            p.msg |= (i % 4) << 24 | (i % 16);
            p.frame = (i % 512) as u64;
            p.len = 50 + i % 400;
            block.push(p);
        }
        block.sort_by_key(|p| p.frame);
        let offs: Vec<Pending> = block.iter().map(|p| Pending { msg: p.msg & !0xF0 & !(0x7F << 16) | 0x80, ..*p }).collect();
        crate::rank::Ranker::new().rank(&mut block, 4096);
        let mut all = block.clone();
        all.extend(offs);
        let mut b = Budget::new(1 << 20);
        b.allow = Some(4500.0);
        for round in 0..3 {
            let t = std::time::Instant::now();
            let mut n = 0;
            b.filter(&all, true, |_, _| n += 1);
            eprintln!("round {round}: {} messages, {n} kept, filter {:.2?} (fast {})", all.len(), t.elapsed(), b.fast);
        }
        let t = std::time::Instant::now();
        let _ = all.clone();
        eprintln!("copying the block: {:.2?}", t.elapsed());
    }

    #[test]
    fn many_tracks_quickly() {
        // a Shinki-sized block: 200,000 note-ons over 5,000 tracks
        let mut b = Budget::new(1 << 20);
        b.allow = Some(20_000.0);
        let mut block: Vec<Pending> =
            (0..200_000u32).map(|i| on((i % 5000) as u16, i % 128, 1 + (i * 7) % 127)).collect();
        let t = std::time::Instant::now();
        let mut n = 0;
        let dropped = b.filter(&mut block, true, |_, _| n += 1);
        assert_eq!(dropped, 180_000);
        assert_eq!(n, 20_000);
        eprintln!("200,000 note-ons chosen in {:.2?}", t.elapsed());
        assert!(t.elapsed().as_millis() < 20);
    }
}
