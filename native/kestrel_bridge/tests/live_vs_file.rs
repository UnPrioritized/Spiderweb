//! The connector's sound = Kestrel's own file render of the same MIDI, sample for sample: its thread, ring and
//! budget (off, and on with room to spare) change nothing. Needs a graphics card (skipped without one).
//! With KB_MIDI + KB_SF set (a real MIDI + soundfont) it also compares those, for KB_SECONDS (default 20).

use std::path::{Path, PathBuf};
use std::sync::Arc;

use kestrel::gpu::GpuSynth;
use kestrel::midi::{Event, MidiStream, MidiWriter, TempoClock};
use kestrel::{Config, Driver};
use kestrel_bridge::{KbOptions, Synth};

const BLOCK: u32 = 512;

/// A file's events as (frame, packed message) by Kestrel's own tempo clock.
fn file_events(path: &Path, rate: u32, end: u64) -> Vec<(u64, u32)> {
    let mut s = MidiStream::open(path).unwrap();
    let mut clock = TempoClock::new(s.division, rate);
    let mut out = Vec::new();
    while let Some((tick, ev)) = s.next() {
        if let Event::Tempo(us) = ev {
            clock.set_tempo(tick, us);
            continue;
        }
        let f = clock.frame_at(tick).max(0.0) as u64;
        if f >= end {
            break;
        }
        let pack = |status: u32, ch: u8, d1: u32, d2: u32| status | (ch as u32 & 15) | d1 << 8 | d2 << 16 | ((ch as u32) >> 4) << 24;
        let msg = match ev {
            Event::NoteOn { ch, key, vel } => pack(0x90, ch, key as u32, vel as u32),
            Event::NoteOff { ch, key } => pack(0x80, ch, key as u32, 0),
            Event::Cc { ch, num, val } => pack(0xB0, ch, num as u32, val as u32),
            Event::Program { ch, val } => pack(0xC0, ch, val as u32, 0),
            Event::PitchBend { ch, val } => {
                let v = (val + 8192) as u32;
                pack(0xE0, ch, v & 0x7F, v >> 7)
            }
            _ => continue,
        };
        out.push((f, msg));
    }
    out
}

fn mixed_midi(dir: &Path) -> PathBuf {
    let mut w = MidiWriter::new(480);
    w.tempo_track(400_000);
    // port 0: chords + a held note with bends and volume
    let mut a: Vec<(u64, Vec<u8>)> = vec![(0, vec![0xC0, 1]), (0, vec![0xB0, 7, 100])];
    for i in 0..40u64 {
        let key = 48 + (i * 7 % 24) as u8;
        a.push((i * 120, vec![0x90, key, 40 + (i * 13 % 80) as u8]));
        a.push((i * 120 + 200, vec![0x80, key, 0]));
    }
    a.push((100, vec![0x91, 60, 110]));
    for i in 0..30u64 {
        a.push((100 + i * 50, vec![0xE1, 0, (64 + i * 2) as u8]));
    }
    a.push((2000, vec![0x81, 60, 0]));
    w.raw_track(a);
    // port 1: a fast run
    let mut b: Vec<(u64, Vec<u8>)> = vec![(0, MidiWriter::port_event(1))];
    for i in 0..200u64 {
        b.push((i * 24, vec![0x92, 60 + (i % 36) as u8, 90]));
        b.push((i * 24 + 30, vec![0x82, 60 + (i % 36) as u8, 0]));
    }
    w.raw_track(b);
    let path = dir.join("mixed.mid");
    w.save(&path).unwrap();
    path
}

/// Kestrel's file render, `blocks` blocks.
fn file_render(cfg: &Config, sf: &Path, midi: &Path, blocks: usize) -> Option<Vec<f32>> {
    let bank = Arc::new(kestrel::session::load_layered(&[sf.to_path_buf()], None, cfg).unwrap());
    let mut synth = GpuSynth::new(cfg, bank.clone()).ok()?;
    let mut d = Driver::open(cfg, bank, midi).unwrap();
    let n = cfg.block_samples();
    let mut all = vec![0.0f32; n * blocks];
    for k in 0..blocks {
        if !d.next_block(&mut synth, &mut all[k * n..(k + 1) * n]).unwrap() {
            break;
        }
    }
    Some(all)
}

/// The connector's render of the same events, started after its first blocks are ready (a real start mid-run).
fn live_render(sf: &Path, events: &[(u64, u32)], blocks: usize, lower: bool) -> Vec<f32> {
    let opts = KbOptions { block_frames: BLOCK, lower_by_itself: lower as u32, ..Default::default() };
    let s = Synth::open(&[sf.to_path_buf()], &opts).unwrap();
    s.wait_ready(opts.ahead_blocks as usize);
    let start = s.stats().rendered;
    assert_eq!(start % BLOCK as u64, 0);
    let frames: Vec<u64> = events.iter().map(|e| e.0 + start).collect();
    let msgs: Vec<u32> = events.iter().map(|e| e.1).collect();
    let tracks: Vec<u16> = events.iter().map(|e| ((e.1 >> 24) & 15) as u16).collect();
    s.push(&frames, &msgs, &tracks);
    let mut skip = vec![0.0f32; start as usize * 2];
    s.fill(&mut skip);
    assert!(skip.iter().all(|&x| x == 0.0));
    let mut out = vec![0.0f32; blocks * BLOCK as usize * 2];
    // pulled a block at a time as an output would, waiting for each to be ready
    for k in 0..blocks {
        let n = BLOCK as usize * 2;
        s.wait_ready(1);
        s.fill(&mut out[k * n..(k + 1) * n]);
    }
    let st = s.stats();
    assert_eq!(st.failed, 0, "{:?}", s.error());
    assert_eq!(st.budget_dropped, 0);
    out
}

fn compare(sf: &Path, midi: &Path, seconds: f64) {
    let cfg = Config {
        block_frames: BLOCK,
        steal_fade_frames: Config::default().steal_fade_frames.min(BLOCK - 1),
        ..Config::default()
    };
    let blocks = (seconds * cfg.sample_rate as f64 / BLOCK as f64) as usize;
    let Some(want) = file_render(&cfg, sf, midi, blocks) else {
        eprintln!("no graphics card: skipped");
        return;
    };
    let events = file_events(midi, cfg.sample_rate, (blocks * BLOCK as usize) as u64);
    for lower in [false, true] {
        let got = live_render(sf, &events, blocks, lower);
        if let Some(i) = (0..want.len()).find(|&i| want[i].to_bits() != got[i].to_bits()) {
            panic!("lower {lower}: sample {i} (block {}): file {} live {}", i / (BLOCK as usize * 2), want[i], got[i]);
        }
        let energy: f64 = want.iter().map(|&x| (x as f64) * (x as f64)).sum();
        assert!(energy > 1.0, "nothing was heard, so nothing was compared");
    }
}

#[test]
fn live_equals_file_render() {
    let dir = std::env::temp_dir().join("kestrel_bridge_test");
    std::fs::create_dir_all(&dir).unwrap();
    let sf = dir.join("rich.sf2");
    kestrel::testkit::rich_sf2(&sf, 48_000).unwrap();
    compare(&sf, &mixed_midi(&dir), 6.0);
}

#[test]
fn live_equals_file_render_real() {
    let (Ok(midi), Ok(sf)) = (std::env::var("KB_MIDI"), std::env::var("KB_SF")) else {
        eprintln!("KB_MIDI / KB_SF not set: skipped");
        return;
    };
    let seconds = std::env::var("KB_SECONDS").ok().and_then(|s| s.parse().ok()).unwrap_or(20.0);
    compare(Path::new(&sf), Path::new(&midi), seconds);
}
