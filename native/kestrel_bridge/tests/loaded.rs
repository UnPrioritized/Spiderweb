//! Soundfonts kept loaded (`FontBank`, `Loaded`, `Synth::open_on`): a synth opened on them sounds exactly like
//! Kestrel's own file render of the same soundfonts stacked, however many times it is opened again and with
//! whatever settings between. Needs a graphics card (skipped without one).

use std::path::{Path, PathBuf};
use std::sync::Arc;

use kestrel::gpu::GpuSynth;
use kestrel::midi::MidiWriter;
use kestrel::{Config, Driver};
use kestrel_bridge::{FontBank, KbOptions, Loaded, Synth};

const BLOCK: u32 = 512;
/// 480 ticks a beat at 0.5 s a beat, 48 kHz = 50 frames a tick: the file's events land on whole frames.
const FRAMES_PER_TICK: u64 = 50;

fn dir() -> PathBuf {
    let d = std::env::temp_dir().join("kestrel_bridge_loaded");
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// Two soundfonts (the second overrides the first's preset) + a MIDI using both channels' notes.
fn files() -> (PathBuf, PathBuf, PathBuf) {
    let d = dir();
    let (a, b) = (d.join("rich.sf2"), d.join("simple.sf2"));
    kestrel::testkit::rich_sf2(&a, 48_000).unwrap();
    kestrel::testkit::simple_sf2(&b, 48_000).unwrap();
    let mut w = MidiWriter::new(480);
    w.tempo_track(500_000);
    let mut t: Vec<(u64, Vec<u8>)> = vec![(0, vec![0xB0, 7, 100])];
    for i in 0..60u64 {
        let key = 48 + (i * 5 % 30) as u8;
        t.push((i * 40, vec![0x90 | (i % 3) as u8, key, 50 + (i * 11 % 70) as u8]));
        t.push((i * 40 + 90, vec![0x80 | (i % 3) as u8, key, 0]));
    }
    w.raw_track(t.clone());
    let midi = d.join("loaded.mid");
    w.save(&midi).unwrap();
    (a, b, midi)
}

/// The same MIDI's events as (frame, packed message).
fn events() -> Vec<(u64, u32)> {
    let mut out = vec![(0, 0xB0 | 7 << 8 | 100 << 16)];
    for i in 0..60u64 {
        let key = 48 + (i * 5 % 30) as u32;
        let ch = (i % 3) as u32;
        out.push((i * 40 * FRAMES_PER_TICK, 0x90 | ch | key << 8 | (50 + (i * 11 % 70) as u32) << 16));
        out.push(((i * 40 + 90) * FRAMES_PER_TICK, 0x80 | ch | key << 8));
    }
    out.sort_by_key(|e| e.0);
    out
}

fn cfg() -> Config {
    Config { block_frames: BLOCK, steal_fade_frames: Config::default().steal_fade_frames.min(BLOCK - 1), ..Config::default() }
}

fn file_render(fonts: &[PathBuf], midi: &Path, blocks: usize) -> Option<Vec<f32>> {
    let cfg = cfg();
    let bank = Arc::new(kestrel::session::load_layered(fonts, None, &cfg).unwrap());
    let mut synth = GpuSynth::new(&cfg, bank.clone()).ok()?;
    let mut d = Driver::open(&cfg, bank, midi).unwrap();
    let n = cfg.block_samples();
    let mut all = vec![0.0f32; n * blocks];
    for k in 0..blocks {
        if !d.next_block(&mut synth, &mut all[k * n..(k + 1) * n]).unwrap() {
            break;
        }
    }
    Some(all)
}

fn opts(voices: u32) -> KbOptions {
    KbOptions { block_frames: BLOCK, max_voices: voices, lower_by_itself: 0, dc_blocker: 0, ..Default::default() }
}

/// A synth's render of the events, started once its first blocks are ready.
fn live_render(s: &Synth, blocks: usize) -> Vec<f32> {
    s.wait_ready(opts(1).ahead_blocks as usize);
    let start = s.stats().rendered;
    let ev = events();
    let frames: Vec<u64> = ev.iter().map(|e| e.0 + start).collect();
    let msgs: Vec<u32> = ev.iter().map(|e| e.1).collect();
    let tracks: Vec<u16> = ev.iter().map(|e| (e.1 & 15) as u16).collect();
    s.push(&frames, &msgs, &tracks);
    let mut skip = vec![0.0f32; start as usize * 2];
    s.fill(&mut skip);
    let n = BLOCK as usize * 2;
    let mut out = vec![0.0f32; blocks * n];
    for k in 0..blocks {
        s.wait_ready(1);
        s.fill(&mut out[k * n..(k + 1) * n]);
    }
    assert_eq!(s.stats().failed, 0, "{:?}", s.error());
    out
}

fn same(want: &[f32], got: &[f32], what: &str) {
    if let Some(i) = (0..want.len()).find(|&i| want[i].to_bits() != got[i].to_bits()) {
        panic!("{what}: sample {i} (block {}): file {} live {}", i / (BLOCK as usize * 2), want[i], got[i]);
    }
    assert!(want.iter().map(|&x| (x as f64) * (x as f64)).sum::<f64>() > 1.0, "nothing was heard");
}

#[test]
fn reopened_on_kept_soundfonts_sounds_the_same() {
    let (a, b, midi) = files();
    let blocks = 6 * 48_000 / BLOCK as usize;
    for fonts in [vec![a.clone()], vec![a.clone(), b.clone()], vec![b.clone(), a.clone()]] {
        let Some(want) = file_render(&fonts, &midi, blocks) else {
            eprintln!("no graphics card: skipped");
            return;
        };
        let banks: Vec<FontBank> = fonts.iter().map(|f| FontBank::load(f, 48_000).unwrap()).collect();
        let loaded = Loaded::new(&banks.iter().collect::<Vec<_>>()).unwrap();
        // a stack keeps no PC copy of its samples (the files' banks hold them): ~1x memory
        assert_eq!(loaded.pc_sample_bytes() == 0, fonts.len() > 1, "{}", loaded.pc_sample_bytes());
        // the files' banks can go: the loaded set keeps what it needs
        drop(banks);
        for (k, voices) in [1 << 20, 4096, 1 << 20].into_iter().enumerate() {
            let s = Synth::open_on(&loaded, &opts(voices)).unwrap();
            same(&want, &live_render(&s, blocks), &format!("{} soundfont(s), open {k}", fonts.len()));
        }
        // a synth outlives its loaded set
        let s = Synth::open_on(&loaded, &opts(1 << 20)).unwrap();
        drop(loaded);
        same(&want, &live_render(&s, blocks), "after the loaded set was freed");
    }
}

#[test]
fn file_banks_stay_whole_after_stacking() {
    // stacking copies: the same file banks stacked again (another order) = Kestrel's own stack of that order
    let (a, b, midi) = files();
    let blocks = 4 * 48_000 / BLOCK as usize;
    let (fa, fb) = (FontBank::load(&a, 48_000).unwrap(), FontBank::load(&b, 48_000).unwrap());
    let first = Loaded::new(&[&fa, &fb]).unwrap();
    drop(first);
    let Some(want) = file_render(&[b.clone(), a.clone()], &midi, blocks) else {
        eprintln!("no graphics card: skipped");
        return;
    };
    let again = Loaded::new(&[&fb, &fa]).unwrap();
    let s = Synth::open_on(&again, &opts(1 << 20)).unwrap();
    same(&want, &live_render(&s, blocks), "stacked again in the other order");
}

#[test]
fn voices_top_is_what_a_bigger_open_is_refused_with() {
    let (a, _, _) = files();
    let fa = FontBank::load(&a, 48_000).unwrap();
    let Ok(loaded) = Loaded::new(&[&fa]) else {
        eprintln!("no graphics card: skipped");
        return;
    };
    let top = loaded.voices_top(&opts(1)).unwrap();
    assert!(top > 1000, "{top}");
    // (refused before anything is made; just over the top can still round into it)
    let err = Synth::open_on(&loaded, &opts(top + top / 10)).err().expect("over the top opened");
    assert!(format!("{err:#}").contains(&format!("max-voices at {top}")), "{err:#}");
    // a wrong sample rate is said, never played at the wrong speed
    let bad = KbOptions { sample_rate: 44_100, ..opts(4096) };
    assert!(Synth::open_on(&loaded, &bad).is_err());
}

#[test]
fn a_broken_soundfont_is_named_without_the_graphics_card() {
    let d = dir();
    let bad = d.join("broken.sf2");
    std::fs::write(&bad, b"RIFF\0\0\0\0sfbkLIST").unwrap();
    assert!(FontBank::load(&bad, 48_000).is_err());
    assert!(FontBank::load(&d.join("not there.sf2"), 48_000).is_err());
}

#[test]
fn the_sound_rows_sound_like_kestrels_own_render_with_them() {
    // 13g: each new KbOptions field = its Kestrel Config field (a file render with the same settings, bit for bit)
    use kestrel::config::AdmitRule;
    use kestrel::{Interpolation, StealRule};
    let (a, _, midi) = files();
    let blocks = 4 * 48_000 / BLOCK as usize;
    let mine = KbOptions {
        interpolation: 2,
        steal_rule: 0,
        release_floor_db: -60.0,
        min_velocity: 60,
        admit_rule: 1,
        max_steal_percent: 50,
        max_layers: 2,
        filter: 0,
        lfo: 0,
        mod_env: 0,
        ..opts(1 << 20)
    };
    let theirs = Config {
        interpolation: Interpolation::Cubic,
        steal_rule: StealRule::Oldest,
        env_floor: 10f32.powf(-60.0 / 20.0),
        min_velocity: 60,
        admit_rule: AdmitRule::Even,
        max_steal_percent: 50,
        max_layers: 2,
        filter_enabled: false,
        lfo_enabled: false,
        mod_env_enabled: false,
        ..cfg()
    };
    let bank = Arc::new(kestrel::session::load_layered(&[a.clone()], None, &theirs).unwrap());
    let Ok(mut synth) = GpuSynth::new(&theirs, bank.clone()) else {
        eprintln!("no graphics card: skipped");
        return;
    };
    let mut d = Driver::open(&theirs, bank, &midi).unwrap();
    let n = theirs.block_samples();
    let mut want = vec![0.0f32; n * blocks];
    for k in 0..blocks {
        if !d.next_block(&mut synth, &mut want[k * n..(k + 1) * n]).unwrap() {
            break;
        }
    }
    // (the filter is decided as the soundfont is read: Filter off = read without)
    let fa = FontBank::load_with(&a, 48_000, false).unwrap();
    let loaded = Loaded::new(&[&fa]).unwrap();
    let s = Synth::open_on(&loaded, &mine).unwrap();
    same(&want, &live_render(&s, blocks), "the sound rows");
    // and they change the sound (velocity under 60 skipped, no filter...): not Kestrel's defaults
    let fd = FontBank::load(&a, 48_000).unwrap();
    let plain = Synth::open_on(&Loaded::new(&[&fd]).unwrap(), &opts(1 << 20)).unwrap();
    assert_ne!(live_render(&plain, blocks), want, "the rows changed nothing");
    // a set read with and without filters isn't stacked
    assert!(Loaded::new(&[&fa, &fd]).is_err());
}

#[test]
fn a_picked_graphics_card_loads_and_a_missing_one_is_said() {
    let (a, _, _) = files();
    let fa = FontBank::load(&a, 48_000).unwrap();
    let Ok((list, _)) = kestrel::gpu::survey(&Config::default()) else { return };
    let Some(card) = list.iter().find(|c| !c.is_software()) else {
        eprintln!("no graphics card: skipped");
        return;
    };
    let backend = format!("{:?}", card.backend).to_ascii_lowercase();
    let loaded = Loaded::new_on(&[&fa], Some(backend.clone()), Some(card.name.clone())).unwrap();
    assert!(Synth::open_on(&loaded, &opts(4096)).is_ok());
    let err = Loaded::new_on(&[&fa], Some(backend), Some("no such card 123".into())).err().expect("opened");
    assert!(format!("{err:#}").contains("no gpu adapter matched"), "{err:#}");
}
