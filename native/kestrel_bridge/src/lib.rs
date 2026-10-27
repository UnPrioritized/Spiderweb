//! Spiderweb's live connection to Kestrel (GPU SoundFont synth by thtstickyboi; live interface by mbms).
//!
//! Spiderweb (Python, ctypes) opens a synth with its soundfonts, pushes MIDI messages with the output frame each
//! should sound at, and BASS pulls the sound through `kb_streamproc` (a BASS STREAMPROC: no Python in the audio
//! path). A render thread keeps a few blocks ready ahead of the output. The note budget ("Lower it by itself")
//! lives here because only here is the GPU time of each block known.
//!
//! Frames: one shared count from 0 at `kb_open`. `rendered` = frames the render thread has made, `handed` =
//! Kestrel frames handed to the output. An event pushed for a frame already rendered sounds at the start of the
//! next block (Kestrel's live rule).

mod budget;

use std::collections::VecDeque;
use std::ffi::{c_char, c_void, CStr, CString};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use kestrel::gpu::GpuSynth;
use kestrel::midi::Event;
use kestrel::{Backend, Config, Driver};

use budget::Budget;

/// What `kb_open` is asked for. `size` = size_of::<KbOptions>() as the caller knows it (fields added later go at
/// the end; a shorter struct gets the defaults for them).
#[repr(C)]
#[derive(Clone, Copy)]
pub struct KbOptions {
    pub size: u32,
    pub sample_rate: u32,
    pub block_frames: u32,
    /// Blocks kept ready ahead of the output (at least 2).
    pub ahead_blocks: u32,
    pub max_voices: u32,
    /// 1 = drop new notes (fair by track, quietest first) to stay in real time; 0 = never (it stutters instead).
    pub lower_by_itself: u32,
}

impl Default for KbOptions {
    fn default() -> Self {
        KbOptions {
            size: std::mem::size_of::<KbOptions>() as u32,
            sample_rate: 48_000,
            block_frames: 512,
            ahead_blocks: 3,
            max_voices: 1 << 20,
            lower_by_itself: 1,
        }
    }
}

/// Counters for the caller (all since `kb_open` unless said).
#[repr(C)]
#[derive(Clone, Copy, Default, Debug)]
pub struct KbStats {
    /// Frames rendered (the render thread's head).
    pub rendered: u64,
    /// Kestrel frames handed to the output.
    pub handed: u64,
    /// Frames of silence handed out because nothing was ready (each one = a stutter).
    pub silence: u64,
    /// Times the output found nothing ready.
    pub late: u64,
    pub voices: u64,
    /// Note-ons the budget dropped (with their note-offs).
    pub budget_dropped: u64,
    /// Note-ons Kestrel itself could not place, and voices it stole for room.
    pub kestrel_dropped: u64,
    pub stolen: u64,
    /// GPU + driver time of the last block, and the worst since the last `kb_stats` call, in microseconds.
    pub last_block_us: u32,
    pub worst_block_us: u32,
    /// Note-ons a block may take now; u32::MAX = no limit.
    pub allowance: u32,
    /// 1 = the render thread stopped on an error (`kb_error` says which); the output is silent.
    pub failed: u32,
}

struct Pending {
    frame: u64,
    msg: u32,
    track: u16,
}

struct Ring {
    blocks: VecDeque<Vec<f32>>,
    /// Samples of the front block already handed out.
    pos: usize,
    spare: Vec<Vec<f32>>,
}

struct Shared {
    inbox: Mutex<VecDeque<Pending>>,
    ring: Mutex<Ring>,
    /// Signalled when the output took a block (room to render) or on close.
    room: Condvar,
    stats: Mutex<KbStats>,
    error: Mutex<Option<String>>,
    stop: AtomicBool,
    lower: AtomicBool,
    ahead: usize,
    /// Worst block since the last kb_stats call.
    worst_us: AtomicU32,
}

pub struct Synth {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
    pub block_frames: u32,
    pub sample_rate: u32,
}

static LAST_ERROR: Mutex<Option<CString>> = Mutex::new(None);

fn set_error(text: &str) {
    *LAST_ERROR.lock().unwrap_or_else(|e| e.into_inner()) =
        Some(CString::new(text.replace('\0', " ")).unwrap_or_default());
}

/// One MIDI message as Spiderweb packs it: status | data1 << 8 | data2 << 16 | port << 24.
pub fn decode(msg: u32) -> Option<Event> {
    let status = (msg & 0xFF) as u8;
    let d1 = ((msg >> 8) & 0x7F) as u8;
    let d2 = ((msg >> 16) & 0x7F) as u8;
    let ch = (((msg >> 24) as u8 % kestrel::midi::PORTS) << 4) | (status & 0x0F);
    Some(match status & 0xF0 {
        0x80 => Event::NoteOff { ch, key: d1 },
        0x90 if d2 == 0 => Event::NoteOff { ch, key: d1 },
        0x90 => Event::NoteOn { ch, key: d1, vel: d2 },
        0xB0 => Event::Cc { ch, num: d1, val: d2 },
        0xC0 => Event::Program { ch, val: d1 },
        0xE0 => Event::PitchBend { ch, val: (((d2 as i16) << 7) | d1 as i16) - 8192 },
        _ => return None,
    })
}

impl Synth {
    pub fn open(fonts: &[PathBuf], opts: &KbOptions) -> Result<Synth> {
        if fonts.is_empty() {
            bail!("no soundfont given");
        }
        let block = opts.block_frames.clamp(64, 16384);
        let cfg = Config {
            sample_rate: opts.sample_rate,
            block_frames: block,
            max_voices: opts.max_voices.max(1),
            steal_fade_frames: Config::default().steal_fade_frames.min(block - 1),
            ..Config::default()
        };
        cfg.validate()?;
        // Each soundfont overrides the ones before it (Spiderweb's list order, top first).
        let bank = Arc::new(kestrel::session::load_layered(fonts, None, &cfg)?);
        let synth = GpuSynth::new(&cfg, bank.clone()).context("the graphics card couldn't start Kestrel")?;
        let driver = Driver::live(&cfg, bank)?;
        let ahead = opts.ahead_blocks.max(2) as usize;
        let shared = Arc::new(Shared {
            inbox: Mutex::new(VecDeque::new()),
            ring: Mutex::new(Ring { blocks: VecDeque::new(), pos: 0, spare: Vec::new() }),
            room: Condvar::new(),
            stats: Mutex::new(KbStats { allowance: u32::MAX, ..Default::default() }),
            error: Mutex::new(None),
            stop: AtomicBool::new(false),
            lower: AtomicBool::new(opts.lower_by_itself != 0),
            ahead,
            worst_us: AtomicU32::new(0),
        });
        let s = shared.clone();
        let rate = cfg.sample_rate;
        let thread = std::thread::Builder::new()
            .name("kestrel render".into())
            .spawn(move || {
                let s2 = s.clone();
                let run = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
                    render_loop(&s2, driver, synth, block, rate)
                }));
                let err = match run {
                    Ok(Ok(())) => None,
                    Ok(Err(e)) => Some(format!("{e:#}")),
                    Err(_) => Some("Kestrel stopped unexpectedly".to_string()),
                };
                if let Some(e) = err {
                    *s.error.lock().unwrap_or_else(|e| e.into_inner()) = Some(e);
                    s.stats.lock().unwrap_or_else(|e| e.into_inner()).failed = 1;
                }
            })?;
        Ok(Synth { shared, thread: Some(thread), block_frames: block, sample_rate: rate })
    }

    /// Queue messages (frames non-decreasing is best; an earlier one is played with the one queued before it).
    pub fn push(&self, frames: &[u64], msgs: &[u32], tracks: &[u16]) {
        let mut q = self.shared.inbox.lock().unwrap();
        for i in 0..frames.len().min(msgs.len()).min(tracks.len()) {
            q.push_back(Pending { frame: frames[i], msg: msgs[i], track: tracks[i] });
        }
    }

    /// Forget every message not rendered yet (sounding notes go on: send note-offs after this).
    pub fn cancel(&self) {
        self.shared.inbox.lock().unwrap().clear();
    }

    pub fn set_lower(&self, on: bool) {
        self.shared.lower.store(on, Ordering::Relaxed);
    }

    pub fn stats(&self) -> KbStats {
        let mut st = *self.shared.stats.lock().unwrap();
        st.worst_block_us = self.shared.worst_us.swap(0, Ordering::Relaxed);
        st
    }

    pub fn error(&self) -> Option<String> {
        self.shared.error.lock().unwrap().clone()
    }

    /// Hand out interleaved stereo samples: what's ready, silence for the rest.
    pub fn fill(&self, out: &mut [f32]) {
        let mut handed = 0u64;
        let mut silent = 0u64;
        {
            let mut guard = self.shared.ring.lock().unwrap();
            let r = &mut *guard;
            let mut i = 0;
            while i < out.len() {
                let Some(front) = r.blocks.front() else {
                    out[i..].fill(0.0);
                    silent = ((out.len() - i) / 2) as u64;
                    break;
                };
                let n = (front.len() - r.pos).min(out.len() - i);
                out[i..i + n].copy_from_slice(&front[r.pos..r.pos + n]);
                i += n;
                r.pos += n;
                handed += (n / 2) as u64;
                if r.pos == front.len() {
                    let b = r.blocks.pop_front().unwrap();
                    r.spare.push(b);
                    r.pos = 0;
                }
            }
        }
        self.shared.room.notify_one();
        let mut st = self.shared.stats.lock().unwrap();
        st.handed += handed;
        if silent > 0 {
            st.silence += silent;
            st.late += 1;
        }
    }

    /// Wait until the render thread has `blocks` blocks ready (tests: a known starting point).
    pub fn wait_ready(&self, blocks: usize) {
        let blocks = blocks.min(self.shared.ahead);
        loop {
            if self.shared.ring.lock().unwrap().blocks.len() >= blocks || self.error().is_some() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
    }
}

impl Drop for Synth {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Relaxed);
        self.shared.room.notify_all();
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

fn render_loop(s: &Shared, mut driver: Driver, mut synth: GpuSynth, block: u32, rate: u32) -> Result<()> {
    let samples = block as usize * 2;
    let block_us = block as f64 * 1e6 / rate as f64;
    let mut budget = Budget::new();
    let mut taken: Vec<Pending> = Vec::new();
    loop {
        let mut out = {
            let mut r = s.ring.lock().unwrap();
            while r.blocks.len() >= s.ahead && !s.stop.load(Ordering::Relaxed) {
                r = s.room.wait(r).unwrap();
            }
            if s.stop.load(Ordering::Relaxed) {
                return Ok(());
            }
            r.spare.pop().unwrap_or_else(|| vec![0.0; samples])
        };
        let end = driver.next_frame() + block as u64;
        {
            let mut q = s.inbox.lock().unwrap();
            while q.front().is_some_and(|p| p.frame < end) {
                taken.push(q.pop_front().unwrap());
            }
        }
        let lower = s.lower.load(Ordering::Relaxed);
        let dropped = budget.filter(&mut taken, lower, |p| {
            if let Some(ev) = decode(p.msg) {
                driver.push(p.frame, ev);
            }
        });
        taken.clear();
        let t = Instant::now();
        driver.render_live(&mut synth, &mut out)?;
        let us = t.elapsed().as_secs_f64() * 1e6;
        budget.measured(us / block_us, lower);
        s.worst_us.fetch_max(us as u32, Ordering::Relaxed);
        let bs = synth.stats();
        {
            let mut st = s.stats.lock().unwrap();
            st.rendered = driver.next_frame();
            st.voices = bs.active_voices;
            st.kestrel_dropped = bs.dropped;
            st.stolen = bs.stolen;
            st.budget_dropped += dropped;
            st.last_block_us = us as u32;
            st.allowance = budget.allowance();
        }
        s.ring.lock().unwrap().blocks.push_back(out);
    }
}

// ---- C ABI (Spiderweb's files/kestrel.py) ----

fn guard<T>(fallback: T, f: impl FnOnce() -> T) -> T {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)).unwrap_or_else(|_| {
        set_error("Kestrel's connector stopped unexpectedly");
        fallback
    })
}

/// "bridge version; Kestrel version + where it came from".
#[no_mangle]
pub extern "C" fn kb_version() -> *const c_char {
    static V: &CStr = c"0.1.0; Kestrel 1.3.0 (mbms0/kestrel-midi 844b002)";
    V.as_ptr()
}

/// The last error text (kb_open failing, or the render thread's), UTF-8; valid until the next kb_* call.
#[no_mangle]
pub extern "C" fn kb_error(h: *const Synth) -> *const c_char {
    guard(std::ptr::null(), || {
        if !h.is_null() {
            if let Some(e) = unsafe { &*h }.error() {
                set_error(&e);
            }
        }
        LAST_ERROR.lock().unwrap_or_else(|e| e.into_inner()).as_ref().map_or(std::ptr::null(), |c| c.as_ptr())
    })
}

/// Open a synth: `fonts` = `n` UTF-8 paths, top of Spiderweb's list first. Null on failure (see kb_error).
/// Takes seconds (soundfonts load): call it off the window's thread.
#[no_mangle]
pub unsafe extern "C" fn kb_open(fonts: *const *const c_char, n: u32, opts: *const KbOptions) -> *mut Synth {
    guard(std::ptr::null_mut(), || {
        let mut o = KbOptions::default();
        if !opts.is_null() {
            // A caller's shorter (older) struct: only the fields it has.
            let size = (*opts).size as usize;
            let len = size.min(std::mem::size_of::<KbOptions>());
            std::ptr::copy_nonoverlapping(opts as *const u8, &mut o as *mut KbOptions as *mut u8, len);
        }
        let mut paths = Vec::new();
        for i in 0..n as usize {
            let p = *fonts.add(i);
            if p.is_null() {
                continue;
            }
            match CStr::from_ptr(p).to_str() {
                Ok(s) => paths.push(PathBuf::from(s)),
                Err(_) => {
                    set_error("a soundfont path isn't UTF-8");
                    return std::ptr::null_mut();
                }
            }
        }
        match Synth::open(&paths, &o) {
            Ok(s) => Box::into_raw(Box::new(s)),
            Err(e) => {
                set_error(&format!("{e:#}"));
                std::ptr::null_mut()
            }
        }
    })
}

/// Queue `n` messages: frames[i] = output frame, msgs[i] = status | d1 << 8 | d2 << 16 | port << 24,
/// tracks[i] = the track the budget shares by.
#[no_mangle]
pub unsafe extern "C" fn kb_push(h: *const Synth, frames: *const u64, msgs: *const u32, tracks: *const u16, n: u32) {
    if h.is_null() || n == 0 {
        return;
    }
    guard((), || {
        let n = n as usize;
        (*h).push(
            std::slice::from_raw_parts(frames, n),
            std::slice::from_raw_parts(msgs, n),
            std::slice::from_raw_parts(tracks, n),
        )
    })
}

#[no_mangle]
pub unsafe extern "C" fn kb_cancel(h: *const Synth) {
    if !h.is_null() {
        guard((), || (*h).cancel())
    }
}

#[no_mangle]
pub unsafe extern "C" fn kb_set_lower(h: *const Synth, on: u32) {
    if !h.is_null() {
        guard((), || (*h).set_lower(on != 0))
    }
}

#[no_mangle]
pub unsafe extern "C" fn kb_stats(h: *const Synth, out: *mut KbStats) {
    if !h.is_null() && !out.is_null() {
        guard((), || *out = (*h).stats())
    }
}

/// Fill `frames` stereo frames of 32-bit float (for tests / writing without BASS).
#[no_mangle]
pub unsafe extern "C" fn kb_fill(h: *const Synth, out: *mut f32, frames: u32) {
    if !h.is_null() && !out.is_null() {
        guard((), || (*h).fill(std::slice::from_raw_parts_mut(out, frames as usize * 2)))
    }
}

/// A BASS STREAMPROC: BASS_StreamCreate(rate, 2, BASS_SAMPLE_FLOAT, kb_streamproc, synth). Free the stream
/// BEFORE kb_close.
#[no_mangle]
pub unsafe extern "system" fn kb_streamproc(_handle: u32, buffer: *mut c_void, length: u32, user: *mut c_void) -> u32 {
    if user.is_null() || buffer.is_null() {
        return 0;
    }
    guard(0, || {
        let samples = length as usize / 4;
        (*(user as *const Synth)).fill(std::slice::from_raw_parts_mut(buffer as *mut f32, samples));
        length
    })
}

/// Stop the render thread and free everything.
#[no_mangle]
pub unsafe extern "C" fn kb_close(h: *mut Synth) {
    if !h.is_null() {
        guard((), || drop(Box::from_raw(h)))
    }
}
