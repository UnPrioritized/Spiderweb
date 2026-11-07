//! Built-in BASSMIDI's limiter (files/synth.py live_limit; fastloops' limit_live + limit_dsp): BASS's sound thread
//! calls sw_limit_dsp itself on each piece of sound (a plain C function: no Python, never waits). The very same sums
//! in the same order as fastloops' limit_live, so the very same sound comes out.
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use crate::maths::{f2i, fmodi, pmax, pmin};

// (synth.py's LIM_* layout: built in here as Numba builds them into its loops; sw_lim_setup checks Python's numbers
// are these, else the library's limiter stays off and the program uses Numba's / Python's)
const BLOCK: i64 = 64;
const DELAY: i64 = 128;
const RING: i64 = 256;
const N: usize = 0;
const ON: usize = 1;
const MIX: usize = 2;
const ENV: usize = 3;
const G0: usize = 4;
const G1: usize = 5;
const PEAK: usize = 6;
const PREV: usize = 7;
const HEAD: usize = 8;
const SIZE: usize = HEAD + 2 * RING as usize;
const SLOTS: usize = 64;
const TOP: usize = 2 * SLOTS;
const ALL: usize = TOP + SLOTS * SIZE;

// (the numbers synth.py works out at start: the fall per block, the fade step, the ceiling)
static FALL: AtomicU64 = AtomicU64::new(0);
static STEP: AtomicU64 = AtomicU64::new(0);
static CEILING: AtomicU64 = AtomicU64::new(0);
static READY: AtomicBool = AtomicBool::new(false);

/// synth.py's numbers: [BLOCK, DELAY, RING, N, ON, MIX, ENV, G0, G1, PEAK, PREV, HEAD, SIZE, SLOTS, TOP, ALL,
/// CEILING, FALL, STEP] -> 0, or FAILED = another layout (the limiter here stays off).
#[no_mangle]
pub unsafe extern "C" fn sw_lim_setup(v: *const f64, n: i64) -> i64 {
    if v.is_null() || n != 19 {
        return crate::FAILED;
    }
    let v = std::slice::from_raw_parts(v, 19);
    let want = [BLOCK as f64, DELAY as f64, RING as f64, N as f64, ON as f64, MIX as f64, ENV as f64, G0 as f64,
                G1 as f64, PEAK as f64, PREV as f64, HEAD as f64, SIZE as f64, SLOTS as f64, TOP as f64, ALL as f64];
    if v[..16] != want {
        return crate::FAILED;
    }
    CEILING.store(v[16].to_bits(), Ordering::Relaxed);
    FALL.store(v[17].to_bits(), Ordering::Relaxed);
    STEP.store(v[18].to_bits(), Ordering::Relaxed);
    READY.store(true, Ordering::Release);
    0
}

/// fastloops.limit_live: st = one slot's row (SIZE numbers), x = the piece (left, right, ... floats, changed in
/// place), loud = every slot's loudness lately (SLOTS), this slot's place in it.
#[inline(always)]
fn live_inner(st: &mut [f64; SIZE], x: &mut [f32], loud: &mut [f64; SLOTS], slot: usize, fall: f64, step: f64,
              ceiling: f64) {
    for fr in x.chunks_exact_mut(2) {
        let mut n = f2i(st[N]);
        let r = HEAD + 2 * fmodi(n, RING) as usize;
        let (left, right) = (fr[0] as f64, fr[1] as f64);
        st[r] = left;
        st[r + 1] = right;
        st[PEAK] = pmax(pmax(st[PEAK], left.abs()), right.abs());
        let d = n - DELAY;
        if st[ON] != 0.0 {
            if d >= 0 {
                st[MIX] = pmin(1.0, st[MIX] + step);
            }
        } else {
            st[MIX] = pmax(0.0, st[MIX] - step);
        }
        let mix = st[MIX];
        if d >= 0 {
            let t = (fmodi(d, BLOCK) + 1) as f64 / BLOCK as f64;
            let gain = st[G0] * (1.0 - t) + st[G1] * t;
            let r = HEAD + 2 * fmodi(d, RING) as usize;
            fr[0] = (left * (1.0 - mix) + st[r] * gain * mix) as f32;
            fr[1] = (right * (1.0 - mix) + st[r + 1] * gain * mix) as f32;
        } else {
            fr[0] = (left * (1.0 - mix)) as f32;
            fr[1] = (right * (1.0 - mix)) as f32;
        }
        n += 1;
        st[N] = n as f64;
        if fmodi(n, BLOCK) == 0 {
            if n >= 2 * BLOCK {
                st[ENV] = pmax(pmax(st[PREV], st[PEAK]), st[ENV] * fall);
                loud[slot] = st[ENV];
                // (Numba's sum: one after another from 0)
                let mut sum = 0.0;
                for &v in loud.iter() {
                    sum += v;
                }
                st[G0] = st[G1];
                st[G1] = ceiling / pmax(sum, ceiling);
            }
            st[PREV] = st[PEAK];
            st[PEAK] = 0.0;
        }
    }
}
level_builds!(fn live_leveled(st: &mut [f64; SIZE], x: &mut [f32], loud: &mut [f64; SLOTS], slot: usize, fall: f64,
                              step: f64, ceiling: f64) => live_inner);

/// The numbers from sw_lim_setup, or None before it.
#[inline(always)]
fn numbers() -> Option<(f64, f64, f64)> {
    if !READY.load(Ordering::Acquire) {
        return None;
    }
    Some((f64::from_bits(FALL.load(Ordering::Relaxed)), f64::from_bits(STEP.load(Ordering::Relaxed)),
          f64::from_bits(CEILING.load(Ordering::Relaxed))))
}

/// fastloops.limit_live for checks (st: SIZE numbers, x: 2 * frames floats, loud: SLOTS) -> 0, or FAILED.
#[no_mangle]
pub unsafe extern "C" fn sw_limit_live(st: *mut f64, x: *mut f32, frames: i64, loud: *mut f64, slot: i64) -> i64 {
    let Some((fall, step, ceiling)) = numbers() else { return crate::FAILED };
    if st.is_null() || loud.is_null() || frames < 0 || (frames > 0 && x.is_null()) || !(0..SLOTS as i64).contains(&slot)
    {
        return crate::FAILED;
    }
    let st = &mut *(st as *mut [f64; SIZE]);
    let loud = &mut *(loud as *mut [f64; SLOTS]);
    let x: &mut [f32] = if frames > 0 { std::slice::from_raw_parts_mut(x, frames as usize * 2) } else { &mut [] };
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        live_leveled(st, x, loud, slot as usize, fall, step, ceiling)
    }));
    if r.is_ok() { 0 } else { crate::FAILED }
}

/// BASS's DSPPROC (fastloops.limit_dsp): the stream's slot found by its handle (none = the sound left as made),
/// user = synth._LIM_ALL (ALL numbers), buf = the piece's floats, length its bytes.
#[no_mangle]
pub unsafe extern "system" fn sw_limit_dsp(_dsp: u32, channel: u32, buf: *mut f32, length: u32, user: *mut f64) {
    let Some((fall, step, ceiling)) = numbers() else { return };
    if user.is_null() || buf.is_null() {
        return;
    }
    let a = std::slice::from_raw_parts_mut(user, ALL);
    let ch = channel as f64;
    for i in 0..SLOTS {
        if a[i] == ch {
            let frames = length as usize / 8;
            let x = std::slice::from_raw_parts_mut(buf, frames * 2);
            let (head, rows) = a.split_at_mut(TOP);
            let loud = &mut *(head[SLOTS..TOP].as_mut_ptr() as *mut [f64; SLOTS]);
            let st = &mut *(rows[i * SIZE..(i + 1) * SIZE].as_mut_ptr() as *mut [f64; SIZE]);
            // (a panic here would cross into BASS: caught, the piece left as it is)
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                live_leveled(st, x, loud, i, fall, step, ceiling)
            }));
            return;
        }
    }
}
