//! Spiderweb's compiled loops: the slowest note work with lots of notes, called from Python through ctypes
//! (scripts/files/native.py). Each loop gives exactly what the NumPy code it replaces gives (dev/tests/native_loops.py
//! compares them); without this library the program uses that code.
//!
//! ONE FILE PER SYSTEM: every loop is built four times inside it, one per x86-64 level (any, SSE4.2, AVX2,
//! AVX-512: `level_builds!`), the highest the processor has picked once when the library is first used (`level`).
//! Rust never fuses a * b + c into one step, so all give the very same numbers.
//!
//! CHEAP CALLS (PLAN.md B rule: never slower than Numba, small inputs too): arrays come in as the NumPy array
//! objects themselves (ctypes py_object, no pointer asked for in Python: that cost ~0.8 us an array) and their
//! place / length are read here from NumPy's array struct (`Arr`; checked once at load by sw_probe, any other
//! layout = the library isn't used). An array of another type, not side by side in memory, or not writable ->
//! NEED_COPY: Python makes plain copies and calls again.
//!
//! THE C EDGE: every exported function returns an i64; below 0 = it didn't run (FAILED: a panic, caught here so it
//! never ends the program; NO_MEMORY; NEED_COPY), and the caller then uses its slower code for that call.

use std::ffi::c_void;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::slice;
use std::sync::atomic::{AtomicI32, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Once, OnceLock};

/// The library's version of this C edge: scripts/files/native.py refuses a library with another one.
pub const ABI: i64 = 4;
pub const FAILED: i64 = -1;
pub const NO_MEMORY: i64 = -2;
pub const NEED_COPY: i64 = -3;

/// Defines `fn name(args) -> r` running `inner(args)`, built once for each x86-64 level (1 any, 2 SSE4.2, 3 AVX2,
/// 4 AVX-512; the inner function and what it calls must be #[inline(always)], so they're built inside each).
/// The feature lists are the standard x86-64-v2 / v3 / v4 ones (LEVEL_2 / 3 / 4 below say the same for checking).
macro_rules! level_builds {
    ($(#[$m:meta])* $vis:vis fn $name:ident($($a:ident: $t:ty),* $(,)?) $(-> $r:ty)? => $inner:path) => {
        $(#[$m])* $vis fn $name($($a: $t),*) $(-> $r)? {
            #[cfg(target_arch = "x86_64")]
            {
                #[target_feature(enable = "sse3,ssse3,sse4.1,sse4.2,popcnt,cmpxchg16b,avx,avx2,bmi1,bmi2,fma,lzcnt,\
                                           f16c,movbe,avx512f,avx512bw,avx512cd,avx512dq,avx512vl")]
                unsafe fn l4($($a: $t),*) $(-> $r)? {
                    $inner($($a),*)
                }
                #[target_feature(enable = "sse3,ssse3,sse4.1,sse4.2,popcnt,cmpxchg16b,avx,avx2,bmi1,bmi2,fma,lzcnt,\
                                           f16c,movbe")]
                unsafe fn l3($($a: $t),*) $(-> $r)? {
                    $inner($($a),*)
                }
                #[target_feature(enable = "sse3,ssse3,sse4.1,sse4.2,popcnt,cmpxchg16b")]
                unsafe fn l2($($a: $t),*) $(-> $r)? {
                    $inner($($a),*)
                }
                match crate::level() {
                    4 => return unsafe { l4($($a),*) },
                    3 => return unsafe { l3($($a),*) },
                    2 => return unsafe { l2($($a),*) },
                    _ => {}
                }
            }
            $inner($($a),*)
        }
    };
}

mod draw;
mod engine;
mod hz;
mod maths;
mod midi_index;
mod midi_scan;
mod misc;
mod order;
mod recolour;
mod stir;
mod tempo;
mod winner;
pub(crate) mod pyext;

// ---------------------------------------------------------------- builds, threads

static LEVEL: AtomicI32 = AtomicI32::new(-1);

/// The highest level this processor has every feature of (1 = any x86-64).
fn best_level() -> i32 {
    #[cfg(target_arch = "x86_64")]
    {
        let l2 = is_x86_feature_detected!("sse3")
            && is_x86_feature_detected!("ssse3")
            && is_x86_feature_detected!("sse4.1")
            && is_x86_feature_detected!("sse4.2")
            && is_x86_feature_detected!("popcnt")
            && is_x86_feature_detected!("cmpxchg16b");
        let l3 = l2
            && is_x86_feature_detected!("avx")
            && is_x86_feature_detected!("avx2")
            && is_x86_feature_detected!("bmi1")
            && is_x86_feature_detected!("bmi2")
            && is_x86_feature_detected!("fma")
            && is_x86_feature_detected!("lzcnt")
            && is_x86_feature_detected!("f16c")
            && is_x86_feature_detected!("movbe");
        let l4 = l3
            && is_x86_feature_detected!("avx512f")
            && is_x86_feature_detected!("avx512bw")
            && is_x86_feature_detected!("avx512cd")
            && is_x86_feature_detected!("avx512dq")
            && is_x86_feature_detected!("avx512vl");
        return if l4 { 4 } else if l3 { 3 } else if l2 { 2 } else { 1 };
    }
    #[allow(unreachable_code)]
    1
}

/// The builds in use: 1 any x86-64, 2 SSE4.2, 3 AVX2, 4 AVX-512.
pub(crate) fn level() -> i32 {
    let l = LEVEL.load(Ordering::Relaxed);
    if l >= 0 {
        return l;
    }
    let l = best_level();
    LEVEL.store(l, Ordering::Relaxed);
    l
}

static THREADS: AtomicUsize = AtomicUsize::new(1);
static LEAST: AtomicUsize = AtomicUsize::new(20000);
static POOL: OnceLock<Option<rayon::ThreadPool>> = OnceLock::new();

/// The shared-out loops' threads (half the cores, sw_setup), made once and kept; None = one core.
pub(crate) fn pool() -> Option<&'static rayon::ThreadPool> {
    let t = THREADS.load(Ordering::Relaxed);
    if t < 2 {
        return None;
    }
    POOL.get_or_init(|| {
        rayon::ThreadPoolBuilder::new().num_threads(t).thread_name(|i| format!("spiderweb loops {i}")).build().ok()
    })
    .as_ref()
}

/// The pool when `many` is asked and there are at least LEAST x times rows (fewer: sharing out costs more than it
/// saves; entering the pool ~25 us, so light loops share out later: times > 1).
pub(crate) fn pool_for(many: bool, n: usize, times: usize) -> Option<&'static rayon::ThreadPool> {
    if many && n >= LEAST.load(Ordering::Relaxed) * times {
        pool()
    } else {
        None
    }
}

/// Runs f; a panic (a bad index, ...) -> FAILED instead of ending the program. Panics say nothing.
fn guard(f: impl FnOnce() -> i64) -> i64 {
    QUIET.call_once(|| std::panic::set_hook(Box::new(|_| {})));
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(FAILED)
}
static QUIET: Once = Once::new();

/// guard for loops handing back a result (Err(FAILED) on a panic).
pub(crate) fn guard_with<R>(f: impl FnOnce() -> Result<R, i64>) -> Result<R, i64> {
    QUIET.call_once(|| std::panic::set_hook(Box::new(|_| {})));
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(Err(FAILED))
}

/// n zeros, or None when there isn't the memory (Rust would end the program). Zeroed by the system (calloc): big
/// ones cost next to nothing until written.
pub(crate) fn try_zeros<T: Zero>(n: usize) -> Option<Vec<T>> {
    if n == 0 {
        return Some(Vec::new());
    }
    let layout = std::alloc::Layout::array::<T>(n).ok()?;
    unsafe {
        let p = std::alloc::alloc_zeroed(layout) as *mut T;
        if p.is_null() {
            return None;
        }
        Some(Vec::from_raw_parts(p, n, n))
    }
}

/// Types whose all-zero bytes are a real value (0).
pub(crate) unsafe trait Zero {}
unsafe impl Zero for i64 {}
unsafe impl Zero for usize {}
unsafe impl Zero for f64 {}

// ---------------------------------------------------------------- NumPy arrays

/// The start of NumPy's array struct (PyArrayObject_fields, the same in NumPy 1 and 2) after Python's object head
/// (refcount + type: a normal, not free-threaded, Python). sw_probe checks it against what NumPy says.
#[repr(C)]
pub struct Arr {
    _refcnt: isize,
    _type: *const c_void,
    data: *mut u8,
    nd: i32,
    dims: *const isize,
    strides: *const isize,
    _base: *const c_void,
    descr: *const Descr,
    flags: i32,
}

/// The start of NumPy's dtype struct (PyArray_Descr: type_num at the same place in NumPy 1 and 2).
#[repr(C)]
pub struct Descr {
    _refcnt: isize,
    _type: *const c_void,
    _typeobj: *const c_void,
    _kind: u8,
    _char: u8,
    byteorder: u8,
    _flags: u8,
    type_num: i32,
}

const WRITEABLE: i32 = 0x400;

/// Element kinds (K_*: their place in KINDS); the NumPy type numbers each one takes come from Python at load
/// (sw_kinds: a bit per type number, e.g. int64 = "long" and "long long" on Linux; int32 = "int" and "long" on
/// Windows). Python's own names for them: native.KIND_TYPES, the same order.
pub(crate) const K_I64: usize = 0;
pub(crate) const K_F64: usize = 1;
pub(crate) const K_U8: usize = 2;
pub(crate) const K_I8: usize = 3;
pub(crate) const K_I16: usize = 4;
pub(crate) const K_U16: usize = 5;
pub(crate) const K_I32: usize = 6;
pub(crate) const K_U32: usize = 7;
pub(crate) const K_U64: usize = 8;
pub(crate) const K_F32: usize = 9;
pub(crate) const K_BOOL: usize = 10;
const NK: usize = 11;
static KINDS: [AtomicU64; NK] = [const { AtomicU64::new(0) }; NK];

/// Element kinds an array can hold.
pub(crate) trait Kind {
    const K: usize;
    #[inline(always)]
    fn ok(t: i32) -> bool {
        (0..64).contains(&t) && KINDS[Self::K].load(Ordering::Relaxed) & (1u64 << t) != 0
    }
}
impl Kind for i64 {
    const K: usize = K_I64;
}
impl Kind for f64 {
    const K: usize = K_F64;
}
impl Kind for u8 {
    const K: usize = K_U8;
}
impl Kind for i8 {
    const K: usize = K_I8;
}
impl Kind for i16 {
    const K: usize = K_I16;
}
impl Kind for u16 {
    const K: usize = K_U16;
}
impl Kind for i32 {
    const K: usize = K_I32;
}
impl Kind for u32 {
    const K: usize = K_U32;
}
impl Kind for u64 {
    const K: usize = K_U64;
}
impl Kind for f32 {
    const K: usize = K_F32;
}
/// A NumPy bool (one byte, 0 or 1).
#[repr(transparent)]
#[derive(Clone, Copy, PartialEq, Eq, Default)]
pub struct Flag(pub u8);
impl Flag {
    #[inline(always)]
    pub fn on(self) -> bool {
        self.0 != 0
    }
}
impl Kind for Flag {
    const K: usize = K_BOOL;
}
unsafe impl Zero for u8 {}
unsafe impl Zero for i32 {}
unsafe impl Zero for u64 {}

/// A 1-D array's (place, length) when it holds T side by side in this processor's byte order; else NEED_COPY.
unsafe fn parts<T: Kind>(o: *const Arr) -> Result<(*mut T, usize), i64> {
    let a = &*o;
    if a.nd != 1 || a.descr.is_null() {
        return Err(NEED_COPY);
    }
    let d = &*a.descr;
    if !T::ok(d.type_num) || d.byteorder == b'>' {
        return Err(NEED_COPY);
    }
    let n = *a.dims;
    if n > 1 && *a.strides != std::mem::size_of::<T>() as isize {
        return Err(NEED_COPY);
    }
    Ok((a.data as *mut T, n.max(0) as usize))
}

/// An N-D array's (place, shape, items) when it holds T in C order, side by side; else NEED_COPY.
unsafe fn parts_nd<T: Kind, const N: usize>(o: *const Arr) -> Result<(*mut T, [usize; N], usize), i64> {
    let a = &*o;
    if a.nd != N as i32 || a.descr.is_null() {
        return Err(NEED_COPY);
    }
    let d = &*a.descr;
    if !T::ok(d.type_num) || d.byteorder == b'>' {
        return Err(NEED_COPY);
    }
    let mut sh = [0usize; N];
    let mut total = 1usize;
    for k in 0..N {
        sh[k] = (*a.dims.add(k)).max(0) as usize;
        total *= sh[k];
    }
    let mut st = std::mem::size_of::<T>() as isize;
    for k in (0..N).rev() {
        if total > 0 && sh[k] > 1 && *a.strides.add(k) != st {  // (an empty one's steps can be anything)
            return Err(NEED_COPY);
        }
        st *= sh[k] as isize;
    }
    Ok((a.data as *mut T, sh, total))
}

/// An N-D array to read: d = its items in C order, sh = its shape.
pub struct Nd<'a, T, const N: usize> {
    pub d: &'a [T],
    pub sh: [usize; N],
}
/// An N-D array to write into.
pub struct NdMut<'a, T, const N: usize> {
    pub d: &'a mut [T],
    pub sh: [usize; N],
}

pub(crate) unsafe fn nd<'a, T: Kind, const N: usize>(o: *const Arr) -> Result<Nd<'a, T, N>, i64> {
    let (p, sh, n) = parts_nd::<T, N>(o)?;
    Ok(Nd { d: if n == 0 || p.is_null() { &[] } else { slice::from_raw_parts(p, n) }, sh })
}

pub(crate) unsafe fn nd_mut<'a, T: Kind, const N: usize>(o: *const Arr) -> Result<NdMut<'a, T, N>, i64> {
    if (*o).flags & WRITEABLE == 0 {
        return Err(NEED_COPY);
    }
    let (p, sh, n) = parts_nd::<T, N>(o)?;
    Ok(NdMut { d: if n == 0 || p.is_null() { &mut [] } else { slice::from_raw_parts_mut(p, n) }, sh })
}

/// A 1-D array to read.
pub(crate) unsafe fn arr<'a, T: Kind>(o: *const Arr) -> Result<&'a [T], i64> {
    let (p, n) = parts::<T>(o)?;
    Ok(if n == 0 || p.is_null() { &[] } else { slice::from_raw_parts(p, n) })
}

/// A 1-D array to write into (NEED_COPY when it can't be written).
pub(crate) unsafe fn arr_mut<'a, T: Kind>(o: *const Arr) -> Result<&'a mut [T], i64> {
    if (*o).flags & WRITEABLE == 0 {
        return Err(NEED_COPY);
    }
    let (p, n) = parts::<T>(o)?;
    Ok(if n == 0 || p.is_null() { &mut [] } else { slice::from_raw_parts_mut(p, n) })
}

macro_rules! take {
    ($e:expr) => {
        match unsafe { $e } {
            Ok(v) => v,
            Err(code) => return code,
        }
    };
}

// ---------------------------------------------------------------- the C edge

/// ABI (the library's C edge version).
#[no_mangle]
pub extern "C" fn sw_abi() -> i64 {
    ABI
}

/// Set once at load: NumPy's type numbers for int64 (two: "long" and "long long" can both be 64 bits) and float64,
/// how many threads the shared-out loops use, and from how many rows they share out. -> the level in use.
#[no_mangle]
pub extern "C" fn sw_setup(i64_a: i64, i64_b: i64, f64_num: i64, threads: i64, least: i64) -> i64 {
    let bit = |t: i64| if (0..64).contains(&t) { 1u64 << t } else { 0 };
    KINDS[K_I64].store(bit(i64_a) | bit(i64_b), Ordering::Relaxed);
    KINDS[K_F64].store(bit(f64_num), Ordering::Relaxed);
    THREADS.store(threads.max(1) as usize, Ordering::Relaxed);
    LEAST.store(least.max(1) as usize, Ordering::Relaxed);
    level() as i64
}

/// Set once at load, after sw_setup: each element kind's NumPy type numbers as a bit each (masks[K_*], n of them).
/// -> 0.
#[no_mangle]
pub unsafe extern "C" fn sw_kinds(masks: *const i64, n: i64) -> i64 {
    let m = slice::from_raw_parts(masks, (n.max(0) as usize).min(NK));
    for (k, v) in m.iter().enumerate() {
        KINDS[k].store(*v as u64, Ordering::Relaxed);
    }
    0
}

/// At load: what this library reads from a NumPy array (place, length, type number, stride, flags) into out (5),
/// so Python can check it reads NumPy's struct right. -> 0.
#[no_mangle]
pub unsafe extern "C" fn sw_probe(o: *const Arr, out: *mut i64) -> i64 {
    let a = &*o;
    let out = slice::from_raw_parts_mut(out, 5);
    out[0] = a.data as i64;
    out[1] = if a.nd >= 1 { *a.dims as i64 } else { -1 };
    out[2] = if a.descr.is_null() { -1 } else { (*a.descr).type_num as i64 };
    out[3] = if a.nd >= 1 { *a.strides as i64 } else { -1 };
    out[4] = a.flags as i64;
    0
}

/// The builds in use: 1 any x86-64, 2 SSE4.2, 3 AVX2, 4 AVX-512.
#[no_mangle]
pub extern "C" fn sw_level() -> i64 {
    level() as i64
}

/// Tests / forcing: use level want (1-4), or the highest this processor has when it has less -> the level now in use.
#[no_mangle]
pub extern "C" fn sw_set_level(want: i64) -> i64 {
    let l = (want.clamp(1, 4) as i32).min(best_level());
    LEVEL.store(l, Ordering::Relaxed);
    l as i64
}

/// Tests: a panic inside, caught -> FAILED.
#[no_mangle]
pub extern "C" fn sw_panic_check() -> i64 {
    guard(|| {
        let v: Vec<i64> = Vec::new();
        v[std::hint::black_box(3)]
    })
}

/// recolour.stir_from's middle for every spot (x, y) -> (ox, oy). fp / ip = the stir's numbers
/// (recolour.stir_numbers). many = shared out over the threads (each spot on its own: the same numbers). -> 0.
#[no_mangle]
pub extern "C" fn sw_stir_spots(x: *const Arr, y: *const Arr, fp: *const Arr, ip: *const Arr, ox: *const Arr,
                                oy: *const Arr, many: i64) -> i64 {
    let (x, y, fp, ip) = (take!(arr::<f64>(x)), take!(arr::<f64>(y)), take!(arr::<f64>(fp)), take!(arr::<i64>(ip)));
    let (ox, oy) = (take!(arr_mut::<f64>(ox)), take!(arr_mut::<f64>(oy)));
    guard(|| stir::spots(x, y, fp, ip, ox, oy, many != 0))
}

/// engine.winner_stretches' pieces (Remove overlaps, split around). order: by group, then start. note / ps / pe:
/// 2 places for each row of order. -> how many pieces.
#[no_mangle]
pub extern "C" fn sw_winner_pieces(group: *const Arr, s: *const Arr, e: *const Arr, rank: *const Arr,
                                   order: *const Arr, note: *const Arr, ps: *const Arr, pe: *const Arr) -> i64 {
    let (group, s, e, rank) = (take!(arr::<i64>(group)), take!(arr::<i64>(s)), take!(arr::<i64>(e)),
                               take!(arr::<i64>(rank)));
    let order = take!(arr::<i64>(order));
    let (note, ps, pe) = (take!(arr_mut::<i64>(note)), take!(arr_mut::<i64>(ps)), take!(arr_mut::<i64>(pe)));
    guard(|| winner::pieces(group, s, e, rank, order, note, ps, pe))
}

/// np.lexsort((c, b, a, key)) -> out. many = the key groups shared out over the threads. -> 0.
#[no_mangle]
pub extern "C" fn sw_key_order(key: *const Arr, a: *const Arr, b: *const Arr, c: *const Arr, out: *const Arr,
                               many: i64) -> i64 {
    let (key, a, b, c) = (take!(arr::<i64>(key)), take!(arr::<i64>(a)), take!(arr::<i64>(b)), take!(arr::<i64>(c)));
    let out = take!(arr_mut::<i64>(out));
    guard(|| order::key_order(key, a, b, c, out, many != 0))
}
