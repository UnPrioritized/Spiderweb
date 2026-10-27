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
    while let Some((tick, ev)) = s.next() {
        if let Event::Tempo(us) = ev {
            clock.set_tempo(tick, us);
            continue;
        }
        if tick < from_beat * ppq {
            continue;
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
    let end = end.min(frames.last().map_or(0, |f| f + 1));
    eprintln!("{} events read in {:.1?}; {:.2} s of song", msgs.len(), t0.elapsed(), end as f64 / rate as f64);

    for lower in [true, false] {
        let opts = KbOptions { max_voices: voices, lower_by_itself: lower as u32, ..Default::default() };
        let block = opts.block_frames as u64;
        let synth = Synth::open(&[PathBuf::from(&sf)], &opts).unwrap();
        synth.wait_ready(opts.ahead_blocks as usize);
        let start = synth.stats().rendered;
        // pulls of 480 frames (10 ms, like a sound device) at real time; events pushed one block ahead of the pull
        let pull = 480usize;
        let mut buf = vec![0.0f32; pull * 2];
        let mut i = 0;
        let mut pulled = 0u64;
        let t = Instant::now();
        let (mut worst_push, mut ticks) = (Duration::ZERO, 0u64);
        let (mut prep_sum, mut block_sum, mut samples_n) = (0u64, 0u64, 0u64);
        // KB_WAV = a folder: what was pulled (stutters included) saved as live-<lower>.wav there
        let wav_dir = std::env::var("KB_WAV").ok();
        let mut heard: Option<Vec<f32>> = wav_dir.as_ref().map(|_| Vec::new());
        while pulled < end {
            let horizon = pulled + (opts.ahead_blocks as u64 + 1) * block;
            let j = frames[i..].partition_point(|&f| f < horizon) + i;
            if j > i {
                let fr: Vec<u64> = frames[i..j].iter().map(|f| f + start).collect();
                let tp = Instant::now();
                synth.push(&fr, &msgs[i..j], &tracks[i..j]);
                worst_push = worst_push.max(tp.elapsed());
                i = j;
            }
            synth.fill(&mut buf);
            if let Some(w) = heard.as_mut() {
                w.extend_from_slice(&buf);
            }
            pulled += pull as u64;
            ticks += 1;
            let st = synth.stats();
            prep_sum += st.last_prep_us as u64;
            block_sum += st.last_block_us as u64;
            samples_n += 1;
            let due = Duration::from_secs_f64(ticks as f64 * pull as f64 / rate as f64);
            if let Some(wait) = due.checked_sub(t.elapsed()) {
                std::thread::sleep(wait);
            }
        }
        let st = synth.stats();
        let notes = msgs.iter().filter(|m| *m & 0xF0 == 0x90 && (*m >> 16) & 0x7F != 0).count();
        eprintln!(
            "lower {lower:5}: {:.2} s of song took {:.2?}; heard {:.2} s, silence {:.2} s in {} late pulls; dropped {} of {notes} \
             note-ons ({:.0} %); last allowance {}; worst push {worst_push:.2?}; voices now {}; block {:.2} ms (prep {:.2})",
            end as f64 / rate as f64,
            t.elapsed(),
            st.handed as f64 / rate as f64 - start as f64 / rate as f64,
            st.silence as f64 / rate as f64,
            st.late,
            st.budget_dropped,
            st.budget_dropped as f64 * 100.0 / notes.max(1) as f64,
            st.allowance,
            st.voices,
            block_sum as f64 / samples_n as f64 / 1000.0,
            prep_sum as f64 / samples_n as f64 / 1000.0
        );
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
