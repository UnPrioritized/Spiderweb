//! Measures (ignored by default): time of one live block made back to back vs with a real-time pause between,
//! and where it goes (Kestrel's per-pass timings). cargo test --release --test pacing -- --ignored --nocapture

use std::sync::Arc;
use std::time::{Duration, Instant};

use kestrel::gpu::GpuSynth;
use kestrel::midi::Event;
use kestrel::{Backend, Config, Driver};

/// A real MIDI (KB_MIDI + KB_SF, KB_SECONDS default 20) paced three ways: back to back, sleeping out each block's
/// real time, spinning it out (CPU never rests).
#[test]
#[ignore = "measures"]
fn pacing_real() {
    use kestrel::midi::{MidiStream, TempoClock};
    let (Ok(midi), Ok(sf)) = (std::env::var("KB_MIDI"), std::env::var("KB_SF")) else {
        eprintln!("KB_MIDI / KB_SF not set");
        return;
    };
    let seconds: f64 = std::env::var("KB_SECONDS").ok().and_then(|s| s.parse().ok()).unwrap_or(20.0);
    let block = 512u32;
    let voices: Vec<u32> = std::env::var("KB_VOICES").map_or(vec![1 << 20], |s| s.split(',').map(|v| v.parse().unwrap()).collect());
    let modes: Vec<String> = std::env::var("KB_MODES").map_or(
        ["back to back", "sleep", "burst 2", "burst 4", "burst 8"].map(String::from).to_vec(),
        |s| s.split(',').map(String::from).collect());
    let cfg = Config { block_frames: block, steal_fade_frames: 96.min(block - 1), ..Config::default() };
    let bank = Arc::new(kestrel::session::load_layered(&[sf.into()], None, &cfg).unwrap());
    let end = (seconds * 48_000.0) as u64;
    // KB_FROM_BEAT: start there (earlier events skipped, tempo kept); at most KB_MOST events kept (memory)
    let from_beat: u64 = std::env::var("KB_FROM_BEAT").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let most: usize = std::env::var("KB_MOST").ok().and_then(|s| s.parse().ok()).unwrap_or(150_000_000);
    let mut s = MidiStream::open(&midi).unwrap();
    let ppq = match s.division {
        kestrel::midi::Division::Ppq(p) => p as u64,
        _ => panic!("SMPTE file"),
    };
    let from_tick = from_beat * ppq;
    let mut clock = TempoClock::new(s.division, 48_000);
    let mut events = Vec::new();
    let mut base: Option<f64> = None;
    let t0 = Instant::now();
    while let Some((tick, ev)) = s.next() {
        if let Event::Tempo(us) = ev {
            clock.set_tempo(tick, us);
            continue;
        }
        if tick < from_tick || !matches!(ev, Event::NoteOn { .. } | Event::NoteOff { .. } | Event::Cc { .. } | Event::Program { .. } | Event::PitchBend { .. }) {
            continue;
        }
        let b = *base.get_or_insert_with(|| clock.frame_at(from_tick));
        let f = (clock.frame_at(tick) - b).max(0.0) as u64;
        if f >= end || events.len() >= most {
            break;
        }
        events.push((f, ev));
    }
    let ons = events.iter().filter(|e| matches!(e.1, Event::NoteOn { .. })).count();
    eprintln!("{} events ({ons} note-ons) read in {:.1?}; last at {:.2} s", events.len(), t0.elapsed(),
        events.last().map_or(0.0, |e| e.0 as f64 / 48_000.0));
    let end = end.min(events.last().map_or(0, |e| e.0 + 1));
    let real = Duration::from_secs_f64(block as f64 / 48_000.0);
    for (mode, max_voices) in modes.iter().flat_map(|m| voices.iter().map(move |&v| (m.as_str(), v))) {
        let cfg = Config { max_voices, ..cfg.clone() };
        let burst: u64 = mode.strip_prefix("burst ").map_or(1, |b| b.parse().unwrap());
        let mut synth = GpuSynth::new(&cfg, bank.clone()).unwrap();
        let mut d = Driver::live(&cfg, bank.clone()).unwrap();
        let mut out = vec![0.0f32; cfg.block_samples()];
        let mut next = events.iter().peekable();
        let mut times = Vec::new();
        let blocks = end / block as u64;
        for k in 0..blocks {
            while let Some(&&(f, ev)) = next.peek() {
                if f >= (k + 1) * block as u64 {
                    break;
                }
                d.push(f, ev);
                next.next();
            }
            let t = Instant::now();
            d.render_live(&mut synth, &mut out).unwrap();
            let took = t.elapsed();
            times.push(took);
            if mode == "sleep" {
                std::thread::sleep(real.saturating_sub(took));
            } else if burst > 1 && k % burst == burst - 1 {
                // the burst's real time, less what it took
                let spent: Duration = times[times.len() - burst as usize..].iter().sum();
                std::thread::sleep((real * burst as u32).saturating_sub(spent));
            }
        }
        times.sort();
        let n = times.len();
        eprintln!("{mode:13} voices {max_voices:8}: median {:.2?}, p90 {:.2?}, p99 {:.2?}, worst {:.2?}; peak {} voices",
            times[n / 2], times[n * 9 / 10], times[n * 99 / 100], times[n - 1], synth.stats().active_voices);
    }
}

#[test]
#[ignore = "measures"]
fn pacing() {
    let dir = std::env::temp_dir().join("kestrel_bridge_pacing");
    std::fs::create_dir_all(&dir).unwrap();
    let sf = dir.join("rich.sf2");
    kestrel::testkit::rich_sf2(&sf, 48_000).unwrap();
    for block in [512u32, 1024] {
        let cfg = Config {
            block_frames: block,
            steal_fade_frames: Config::default().steal_fade_frames.min(block - 1),
            profile: true,
            ..Config::default()
        };
        let bank = Arc::new(kestrel::session::load_layered(&[sf.clone()], None, &cfg).unwrap());
        let mut synth = GpuSynth::new(&cfg, bank.clone()).unwrap();
        let mut d = Driver::live(&cfg, bank).unwrap();
        let mut out = vec![0.0f32; cfg.block_samples()];
        let real = Duration::from_secs_f64(block as f64 / 48_000.0);
        for (name, pause) in [("back to back", false), ("paced", true), ("back to back", false)] {
            let mut times = Vec::new();
            for k in 0..300u64 {
                if k % 20 == 0 {
                    d.push(d.next_frame(), Event::NoteOn { ch: 0, key: 60 + (k / 20 % 12) as u8, vel: 100 });
                }
                let t = Instant::now();
                d.render_live(&mut synth, &mut out).unwrap();
                times.push(t.elapsed());
                if pause {
                    std::thread::sleep(real.saturating_sub(t.elapsed()));
                }
            }
            times.sort();
            eprintln!(
                "block {block} {name:13}: median {:.2?}, p90 {:.2?}, worst {:.2?}; passes {:?}",
                times[times.len() / 2],
                times[times.len() * 9 / 10],
                times[times.len() - 1],
                synth.timings().iter().map(|(n, t)| format!("{n} {t:.2}")).collect::<Vec<_>>()
            );
        }
    }
}
