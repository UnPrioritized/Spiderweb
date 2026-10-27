//! Measures (ignored by default): a real MIDI through the connector with the output pulled in real time, as
//! BASS would, "Lower it by itself" on and off: stutters (late pulls, silence) and notes dropped.
//! KB_MIDI, KB_SF, KB_SECONDS (10), KB_FROM_BEAT (0), KB_VOICES (Kestrel's default), KB_MOST (150 M events).
//! cargo test --release --test realtime -- --ignored --nocapture

use std::path::PathBuf;
use std::time::{Duration, Instant};

use kestrel::midi::{Event, MidiStream, TempoClock};
use kestrel_bridge::{KbOptions, Synth};

fn pack(ev: &Event) -> Option<u32> {
    let p = |status: u32, ch: u8, d1: u32, d2: u32| Some(status | (ch as u32 & 15) | d1 << 8 | d2 << 16 | ((ch as u32) >> 4) << 24);
    match *ev {
        Event::NoteOn { ch, key, vel } => p(0x90, ch, key as u32, vel as u32),
        Event::NoteOff { ch, key } => p(0x80, ch, key as u32, 0),
        Event::Cc { ch, num, val } => p(0xB0, ch, num as u32, val as u32),
        Event::Program { ch, val } => p(0xC0, ch, val as u32, 0),
        Event::PitchBend { ch, val } => {
            let v = (val + 8192) as u32;
            p(0xE0, ch, v & 0x7F, v >> 7)
        }
        _ => None,
    }
}

#[test]
#[ignore = "measures"]
fn realtime() {
    let (Ok(midi), Ok(sf)) = (std::env::var("KB_MIDI"), std::env::var("KB_SF")) else {
        eprintln!("KB_MIDI / KB_SF not set");
        return;
    };
    let env = |k: &str, d: f64| std::env::var(k).ok().and_then(|s| s.parse().ok()).unwrap_or(d);
    let seconds = env("KB_SECONDS", 10.0);
    let from_beat = env("KB_FROM_BEAT", 0.0) as u64;
    let voices = env("KB_VOICES", (1u32 << 20) as f64) as u32;
    let most = env("KB_MOST", 150e6) as usize;
    let rate = 48_000u32;
    let end = (seconds * rate as f64) as u64;
    let mut s = MidiStream::open(&midi).unwrap();
    let ppq = match s.division {
        kestrel::midi::Division::Ppq(p) => p as u64,
        _ => panic!("SMPTE file"),
    };
    let mut clock = TempoClock::new(s.division, rate);
    let (mut frames, mut msgs, mut tracks) = (Vec::new(), Vec::new(), Vec::new());
    let mut base = None;
    let t0 = Instant::now();
    // controllers / programs / bends in effect at the start (as Spiderweb sends them when it starts mid-song):
    // the last value of each per channel before it, sent at frame 0
    let mut state: std::collections::BTreeMap<(u32, u32), u32> = Default::default();
    let mut state_sent = from_beat == 0;
    // KB_CACHE=file: the stretch's events saved there once (the MIDI isn't changed), read from it after
    let cache = std::env::var("KB_CACHE").ok();
    let cached = cache.as_ref().and_then(|p| std::fs::read(p).ok());
    if let Some(b) = &cached {
        let n = u64::from_le_bytes(b[..8].try_into().unwrap()) as usize;
        let (f, rest) = b[8..].split_at(n * 8);
        let (m, t) = rest.split_at(n * 4);
        frames = f.chunks_exact(8).map(|c| u64::from_le_bytes(c.try_into().unwrap())).collect();
        msgs = m.chunks_exact(4).map(|c| u32::from_le_bytes(c.try_into().unwrap())).collect();
        tracks = t.chunks_exact(2).map(|c| u16::from_le_bytes(c.try_into().unwrap())).collect();
        eprintln!("{n} events from the cache");
    }
    while let Some((tick, ev)) = s.next().filter(|_| cached.is_none()) {
        if let Event::Tempo(us) = ev {
            clock.set_tempo(tick, us);
            continue;
        }
        if tick < from_beat * ppq {
            if let Some(m) = pack(&ev) {
                let kind = match m & 0xF0 {
                    0xB0 => Some((m & 15 | ((m >> 24) & 15) << 4, (m >> 8) & 0x7F)),
                    0xC0 => Some((m & 15 | ((m >> 24) & 15) << 4, 1000)),
                    0xE0 => Some((m & 15 | ((m >> 24) & 15) << 4, 1001)),
                    _ => None,
                };
                if let Some(k) = kind {
                    state.insert(k, m);
                }
            }
            continue;
        }
        if !state_sent {
            state_sent = true;
            eprintln!("{} controller / program / bend values in effect at the start, sent first", state.len());
            for &m in state.values() {
                frames.push(0);
                msgs.push(m);
                tracks.push(((m & 15) | ((m >> 24) & 15) << 4) as u16);
            }
        }
        let Some(m) = pack(&ev) else { continue };
        let b = *base.get_or_insert_with(|| clock.frame_at(from_beat * ppq));
        let f = (clock.frame_at(tick) - b).max(0.0) as u64;
        if f >= end || msgs.len() >= most {
            break;
        }
        frames.push(f);
        msgs.push(m);
        // (the reader doesn't say which track: the channel + port stands in for it here)
        tracks.push(((m & 15) | ((m >> 24) & 15) << 4) as u16);
    }
    if let (Some(p), None) = (&cache, &cached) {
        let mut b = (frames.len() as u64).to_le_bytes().to_vec();
        frames.iter().for_each(|f| b.extend_from_slice(&f.to_le_bytes()));
        msgs.iter().for_each(|m| b.extend_from_slice(&m.to_le_bytes()));
        tracks.iter().for_each(|t| b.extend_from_slice(&t.to_le_bytes()));
        std::fs::write(p, b).unwrap();
        eprintln!("saved {} events to {p}", frames.len());
    }
    // (a cache longer than this run: cut at its end)
    let n = frames.partition_point(|&f| f < end);
    frames.truncate(n);
    msgs.truncate(n);
    tracks.truncate(n);
    // KB_KEY=N or LO-HI: only those keys' notes
    if let Ok(ks) = std::env::var("KB_KEY") {
        let (lo, hi) = ks.split_once('-').unwrap_or((&ks, &ks));
        let (lo, hi): (u32, u32) = (lo.parse().unwrap(), hi.parse().unwrap());
        let keep: Vec<bool> =
            msgs.iter().map(|&m| m & 0xE0 != 0x80 || (lo..=hi).contains(&((m >> 8) & 0x7F))).collect();
        let mut k = keep.iter();
        frames.retain(|_| *k.next().unwrap());
        let mut k = keep.iter();
        tracks.retain(|_| *k.next().unwrap());
        let mut k = keep.iter();
        msgs.retain(|_| *k.next().unwrap());
    }
    // KB_COPIES=1: exact copies (same frame + message) sent once with their count, their note-offs left out (the
    // next ones of their channel + key), as Spiderweb's feeder does
    // KB_MINVEL=N: note-ons under velocity N left out with their note-offs (the oldest-first one each)
    let minvel = env("KB_MINVEL", 0.0) as u32;
    if minvel > 0 {
        let n0 = msgs.len();
        let mut open: Vec<std::collections::VecDeque<bool>> = vec![Default::default(); 256 * 128];
        let mut keep = vec![true; msgs.len()];
        for (i, &m) in msgs.iter().enumerate() {
            let slot = (((m >> 24) & 15) << 4 | (m & 15)) as usize * 128 + ((m >> 8) & 0x7F) as usize;
            if m & 0xF0 == 0x90 && (m >> 16) & 0x7F != 0 {
                let out = (m >> 16) & 0x7F < minvel;
                open[slot].push_back(out);
                keep[i] = !out;
            } else if m & 0xF0 == 0x80 || m & 0xF0 == 0x90 {
                keep[i] = open[slot].pop_front() != Some(true);
            }
        }
        let mut k = keep.iter();
        frames.retain(|_| *k.next().unwrap());
        let mut k = keep.iter();
        tracks.retain(|_| *k.next().unwrap());
        let mut k = keep.iter();
        msgs.retain(|_| *k.next().unwrap());
        eprintln!("min velocity {minvel}: {n0} events -> {}", msgs.len());
    }
    let mut copies: Vec<u32> = Vec::new();
    if env("KB_COPIES", 0.0) != 0.0 {
        let n0 = msgs.len();
        let (mut f2, mut m2, mut t2) = (Vec::new(), Vec::new(), Vec::new());
        let mut skip = vec![0u32; 256 * 128];
        let mut here: std::collections::HashMap<u32, usize> = Default::default();
        let mut at = u64::MAX;
        for i in 0..msgs.len() {
            let (f, m) = (frames[i], msgs[i]);
            if f != at {
                here.clear();
                at = f;
            }
            let slot = (((m >> 24) & 15) << 4 | (m & 15)) as usize * 128 + ((m >> 8) & 0x7F) as usize;
            if m & 0xF0 == 0x90 && (m >> 16) & 0x7F != 0 {
                if let Some(&j) = here.get(&m) {
                    copies[j] += 1;
                    skip[slot] += 1;
                    continue;
                }
                here.insert(m, m2.len());
            } else if (m & 0xF0 == 0x80 || m & 0xF0 == 0x90) && skip[slot] > 0 {
                skip[slot] -= 1;
                continue;
            }
            f2.push(f);
            m2.push(m);
            t2.push(tracks[i]);
            copies.push(1);
        }
        (frames, msgs, tracks) = (f2, m2, t2);
        eprintln!("copies: {n0} events -> {}", msgs.len());
    }
    let end = end.min(frames.last().map_or(0, |f| f + 1));
    // each note-on's length, from the whole stretch (FIFO per channel + key), as Spiderweb would know it;
    // KB_LENS=0 = not given (the connector pairs inside each push)
    let mut lens = vec![u32::MAX; msgs.len()];
    {
        let mut open: Vec<std::collections::VecDeque<usize>> = vec![Default::default(); 256 * 128];
        for (i, &m) in msgs.iter().enumerate() {
            let slot = (((m >> 24) & 15) << 4 | (m & 15)) as usize * 128 + ((m >> 8) & 0x7F) as usize;
            if m & 0xF0 == 0x90 && (m >> 16) & 0x7F != 0 {
                open[slot].push_back(i);
            } else if m & 0xF0 == 0x80 || m & 0xF0 == 0x90 {
                if let Some(j) = open[slot].pop_front() {
                    lens[j] = (frames[i] - frames[j]).min(u32::MAX as u64 - 1) as u32;
                }
            }
        }
    }
    let give_lens = env("KB_LENS", 1.0) != 0.0;
    // KB_CC=1: which controllers the stretch uses, per number: messages, channels, values seen (lowest..highest)
    if env("KB_CC", 0.0) != 0.0 {
        let mut per: std::collections::BTreeMap<u32, (u64, std::collections::BTreeSet<u32>, u32, u32)> = Default::default();
        for &m in &msgs {
            if m & 0xF0 == 0xB0 {
                let e = per.entry((m >> 8) & 0x7F).or_insert((0, Default::default(), 127, 0));
                e.0 += 1;
                e.1.insert((m & 15) | ((m >> 24) & 15) << 4);
                e.2 = e.2.min((m >> 16) & 0x7F);
                e.3 = e.3.max((m >> 16) & 0x7F);
            }
        }
        for (num, (n, chans, lo, hi)) in per {
            eprintln!("CC {num:3}: {n} messages on {} channels, values {lo}..{hi}", chans.len());
        }
        return;
    }
    if let Some(b) = base {
        eprintln!("beat {from_beat} starts at {:.4} s of the song", b / rate as f64);
    }
    eprintln!("{} events read in {:.1?}; {:.2} s of song", msgs.len(), t0.elapsed(), end as f64 / rate as f64);

    // KB_TAIL: frames a note sounds after its off, for the merging (rank::TAIL)
    if let Ok(t) = std::env::var("KB_TAIL") {
        kestrel_bridge::set_tail(t.parse().unwrap());
    }
    // KB_ONLY=on / off: run only that half
    let only = std::env::var("KB_ONLY").unwrap_or_default();
    for lower in [true, false] {
        if (only == "on" && !lower) || (only == "off" && lower) {
            continue;
        }
        let ahead = env("KB_AHEAD", KbOptions::default().ahead_blocks as f64) as u32;
        // KB_CEILING: the limiter's ceiling in dBFS (the user's reference: -1)
        let ceiling = env("KB_CEILING", 0.0) as f32;
        let opts = KbOptions {
            max_voices: voices,
            lower_by_itself: lower as u32,
            ahead_blocks: ahead,
            limiter_ceiling_db: ceiling,
            // KB_LIMITER=0: no limiter (what the notes themselves add up to)
            limiter: env("KB_LIMITER", 1.0) as u32,
            ..Default::default()
        };
        let block = opts.block_frames as u64;
        let synth = Synth::open(&[PathBuf::from(&sf)], &opts).unwrap();
        // KB_OFFLINE=N: no clock (pulled as soon as made), "on" fixed at N note-ons a block, "off" every note: the
        // note choice + merging measured with no real-time timing in it
        let offline = env("KB_OFFLINE", 0.0) as u32;
        if offline > 0 && lower {
            // KB_MERGE=0: dropped notes' loudness not carried (what the merging itself does)
            synth.fix_allowance(offline, env("KB_MERGE", 1.0) != 0.0);
        }
        synth.wait_ready(opts.ahead_blocks as usize);
        let start = synth.stats().rendered;
        // pulls of 480 frames (10 ms, like a sound device) at real time; events pushed one block ahead of the pull
        let pull = 480usize;
        let mut buf = vec![0.0f32; pull * 2];
        let mut pulled = 0u64;
        let t = Instant::now();
        let mut ticks = 0u64;
        let (mut prep_sum, mut block_sum, mut samples_n) = (0u64, 0u64, 0u64);
        // KB_WAV = a folder: what was pulled (stutters included) saved as live-<lower>.wav there
        let wav_dir = std::env::var("KB_WAV").ok();
        let mut heard: Option<Vec<f32>> = wav_dir.as_ref().map(|_| Vec::new());
        let done = std::sync::atomic::AtomicBool::new(false);
        // (offline: the pulls wait for the sender, else the render runs ahead of the notes; the sender goes by the
        // pulls' own count, `pulled_at`, in its frames)
        let sent_to = std::sync::atomic::AtomicU64::new(0);
        let pulled_at = std::sync::atomic::AtomicU64::new(0);
        let worst_push = std::thread::scope(|sc| {
            // the sender, on its own thread as in Spiderweb: whole blocks a push (a block ranked in one push is
            // chosen the fast way), KB_CHUNK blocks at a time (note ends inside a push are known), ahead of what
            // was handed out
            let sender = sc.spawn(|| {
                let chunk = env("KB_CHUNK", 1.0) as u64;
                let (mut i, mut worst) = (0usize, Duration::ZERO);
                while !done.load(std::sync::atomic::Ordering::Relaxed) && i < frames.len() {
                    let at = if offline > 0 {
                        pulled_at.load(std::sync::atomic::Ordering::Relaxed)
                    } else {
                        synth.stats().handed.saturating_sub(start)
                    };
                    // (a whole chunk ahead: ranking one takes time, its notes must not arrive late)
                    // (offline one more: the render thread makes `ahead` blocks past the pulls)
                    let extra = if offline > 0 { 1 } else { 0 };
                    let horizon = ((at / block + opts.ahead_blocks as u64 + 1) / chunk + 2 + extra) * chunk * block;
                    let j = frames[i..].partition_point(|&f| f < horizon) + i;
                    if j > i {
                        let fr: Vec<u64> = frames[i..j].iter().map(|f| f + start).collect();
                        let tp = Instant::now();
                        synth.push_full(&fr, &msgs[i..j], &tracks[i..j], give_lens.then(|| &lens[i..j]),
                            (!copies.is_empty()).then(|| &copies[i..j]));
                        worst = worst.max(tp.elapsed());
                        i = j;
                        sent_to.store(if i == frames.len() { u64::MAX } else { horizon }, std::sync::atomic::Ordering::Relaxed);
                    } else {
                        std::thread::sleep(Duration::from_millis(1));
                    }
                }
                worst
            });
        while pulled < end {
            if offline > 0 {
                // (in the sender's frames: counted from the first note, after the `start` frames made before it)
                pulled_at.store(pulled.saturating_sub(start), std::sync::atomic::Ordering::Relaxed);
                let need = pulled.saturating_sub(start) + (opts.ahead_blocks as u64 + 2) * block;
                let tw = Instant::now();
                while sent_to.load(std::sync::atomic::Ordering::Relaxed) < need {
                    std::thread::sleep(Duration::from_micros(200));
                    if tw.elapsed() > Duration::from_secs(5) {
                        eprintln!("offline: waiting for the sender: need {need}, sent to {}, handed {}",
                            sent_to.load(std::sync::atomic::Ordering::Relaxed), synth.stats().handed);
                        break;
                    }
                }
                let tw = Instant::now();
                // (two: a pull may straddle the block being read and the next)
                synth.wait_ready(2);
                if tw.elapsed() > Duration::from_secs(5) {
                    eprintln!("offline: a block took {:.1?}: {:?}", tw.elapsed(), synth.stats());
                }
            }
            synth.fill(&mut buf);
            if let Some(w) = heard.as_mut() {
                w.extend_from_slice(&buf);
            }
            pulled += pull as u64;
            ticks += 1;
            let st = synth.stats();
            if offline > 0 && ticks % 100 == 0 {
                eprintln!("offline {}: pulled {:.2} s in {:.1?}, sent to {:.2} s", if lower { "on" } else { "off" },
                    pulled as f64 / rate as f64, t.elapsed(),
                    sent_to.load(std::sync::atomic::Ordering::Relaxed).min(u64::MAX / 2) as f64 / rate as f64);
            }
            if lower && ticks % 25 == 0 {
                eprint!("{:.2}s: allow {} cap {} voices {} block {:.1}ms cuts {} | ", pulled as f64 / rate as f64, st.allowance,
                    st.voice_cap, st.voices, st.last_block_us as f64 / 1000.0, st.cuts);
            }
            prep_sum += st.last_prep_us as u64;
            block_sum += st.last_block_us as u64;
            samples_n += 1;
            let due = Duration::from_secs_f64(ticks as f64 * pull as f64 / rate as f64);
            if let Some(wait) = due.checked_sub(t.elapsed()).filter(|_| offline == 0) {
                std::thread::sleep(wait);
            }
        }
            done.store(true, std::sync::atomic::Ordering::Relaxed);
            sender.join().unwrap()
        });
        let st = synth.stats();
        let notes = msgs.iter().filter(|m| *m & 0xF0 == 0x90 && (*m >> 16) & 0x7F != 0).count();
        eprintln!(
            "lower {lower:5}: {:.2} s of song took {:.2?}; heard {:.2} s, silence {:.2} s in {} late pulls; dropped {} of {notes} \
             note-ons ({:.0} %); keys kept {:.1} % ; last allowance {}; worst push {worst_push:.2?}; voices now {}, stolen {}, kestrel dropped {}; block {:.2} ms (prep {:.2})",
            end as f64 / rate as f64,
            t.elapsed(),
            st.handed as f64 / rate as f64 - start as f64 / rate as f64,
            st.silence as f64 / rate as f64,
            st.late,
            st.budget_dropped,
            st.budget_dropped as f64 * 100.0 / notes.max(1) as f64,
            st.keys_kept as f64 * 100.0 / st.keys_wanted.max(1) as f64,
            st.allowance,
            st.voices,
            st.stolen,
            st.kestrel_dropped,
            block_sum as f64 / samples_n as f64 / 1000.0,
            prep_sum as f64 / samples_n as f64 / 1000.0
        );
        {
            let p: Vec<f64> = kestrel_bridge::PROFILE.iter().map(|a| a.swap(0, std::sync::atomic::Ordering::Relaxed) as f64).collect();
            let (pushes, blocks) = (p[5].max(1.0), p[6].max(1.0));
            eprintln!("   per push ({pushes}): copy {:.2} ms, pairing {:.2}, ranking {:.2} | per block ({blocks}): choose + hand {:.2} ms, Kestrel render {:.2}",
                p[0] / pushes / 1e6, p[1] / pushes / 1e6, p[2] / pushes / 1e6, p[3] / blocks / 1e6, p[4] / blocks / 1e6);        }
        eprintln!("   fast blocks {}, note-ons carrying merged copies {} (factor mean {:.2}, most {:.1})", st.fast_blocks,
            st.merged, st.merge_mean, st.merge_max);
        assert_eq!(st.failed, 0, "{:?}", synth.error());
        if let (Some(dir), Some(samples)) = (&wav_dir, &heard) {
            let path = std::path::Path::new(dir).join(format!("live-{}.wav", if lower { "lower-on" } else { "lower-off" }));
            let pcm: Vec<u8> = samples.iter().flat_map(|x| ((x.clamp(-1.0, 1.0) * 32767.0) as i16).to_le_bytes()).collect();
            let mut f = Vec::with_capacity(44 + pcm.len());
            f.extend_from_slice(b"RIFF");
            f.extend_from_slice(&(36 + pcm.len() as u32).to_le_bytes());
            f.extend_from_slice(b"WAVEfmt ");
            f.extend_from_slice(&16u32.to_le_bytes());
            f.extend_from_slice(&1u16.to_le_bytes());
            f.extend_from_slice(&2u16.to_le_bytes());
            f.extend_from_slice(&rate.to_le_bytes());
            f.extend_from_slice(&(rate * 4).to_le_bytes());
            f.extend_from_slice(&4u16.to_le_bytes());
            f.extend_from_slice(&16u16.to_le_bytes());
            f.extend_from_slice(b"data");
            f.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
            f.extend_from_slice(&pcm);
            std::fs::write(&path, f).unwrap();
            eprintln!("saved {}", path.display());
        }
    }
}
