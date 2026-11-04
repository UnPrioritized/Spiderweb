//! Dev only (PLAN D measuring): one piece MIDI made by Merlin as Spiderweb makes it, with Merlin's own log lines
//! timed. cargo run --release --example merlin_time -- <soundfont> <piece.mid> <skip frames> <max frames>

use std::time::{Duration, Instant};

use kestrel_bridge::piece::{Engine, Piece, KbPieceStatus};
use kestrel_bridge::{FontBank, KbOptions, Loaded};

struct Timed(Instant);

impl log::Log for Timed {
    fn enabled(&self, m: &log::Metadata) -> bool {
        m.level() <= log::Level::Info
    }
    fn log(&self, r: &log::Record) {
        if self.enabled(r.metadata()) {
            eprintln!("{:8.3}s {}: {}", self.0.elapsed().as_secs_f64(), r.target(), r.args());
        }
    }
    fn flush(&self) {}
}

fn main() -> anyhow::Result<()> {
    let a: Vec<String> = std::env::args().collect();
    let logger: &'static Timed = Box::leak(Box::new(Timed(Instant::now())));
    log::set_logger(logger).unwrap();
    log::set_max_level(log::LevelFilter::Info);
    let font = FontBank::load_with(std::path::Path::new(&a[1]), 48_000, true)?;
    let loaded = Loaded::new_on(&[&font], None, None)?;
    let (skip, max): (u64, u64) = (a[3].parse()?, a[4].parse()?);
    let times: usize = a.get(5).map_or(Ok(1), |s| s.parse())?;
    let mut first: Option<Vec<f32>> = None;
    for _ in 0..times {
        eprintln!("{:8.3}s --- piece starts", logger.0.elapsed().as_secs_f64());
        let t = Instant::now();
        let p = Piece::start(&loaded, std::path::Path::new(&a[2]), &KbOptions::default(), Engine::Merlin, skip, max)?;
        let state = p.wait(Duration::from_secs(3600));
        let st: KbPieceStatus = p.status();
        let took = t.elapsed().as_secs_f64();
        let (ptr, n) = p.audio();
        let audio: Vec<f32> = if ptr.is_null() { Vec::new() } else { unsafe { std::slice::from_raw_parts(ptr, n * 2) }.to_vec() };
        let same = match &first {
            None => "first".to_string(),
            Some(f) => format!("same as the first: {}", f == &audio),
        };
        println!("piece: state {state}, {} frames, {took:.2} s, {same}", st.frames);
        if first.is_none() {
            first = Some(audio);
        }
        if let Some(e) = p.error() {
            eprintln!("error: {e}");
        }
    }
    Ok(())
}
