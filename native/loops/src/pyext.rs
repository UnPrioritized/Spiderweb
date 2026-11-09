//! The same loops as a Python extension module (stable ABI 3, METH_FASTCALL; scripts/files/native.py load_ext loads
//! this very file as one): a tiny call ~0.4 us, where ctypes' own floor is ~1 us and Numba's ~0.8 us (PLAN.md B:
//! never slower than Numba). The file stays a ctypes library too (sw_* functions: used when this can't load).
//! Same arguments as the sw_* functions; they're called as they are. The GIL is let go only for big inputs
//! (GIL_FREE items: letting go + taking it back costs ~0.1 us; notes are made on a thread mid-drag, so the window
//! must keep running through long calls). python3.dll is linked by name (raw-dylib: no import library needed).

use crate::{Arr, NEED_COPY};
use std::ffi::{c_char, c_int, c_void};
use std::ptr::{addr_of_mut, null, null_mut};
use std::sync::atomic::{AtomicPtr, Ordering};

type PyObj = c_void;
const METH_FASTCALL: c_int = 0x0080;
const GIL_FREE: isize = 2048;

#[repr(C)]
struct Head {
    refcnt: isize,
    typ: *mut c_void,
}
#[repr(C)]
struct DefBase {
    ob_base: Head,
    m_init: *const c_void,
    m_index: isize,
    m_copy: *mut c_void,
}
#[repr(C)]
struct MethodDef {
    name: *const c_char,
    meth: *const c_void,
    flags: c_int,
    doc: *const c_char,
}
#[repr(C)]
struct ModuleDef {
    base: DefBase,
    name: *const c_char,
    doc: *const c_char,
    size: isize,
    methods: *mut MethodDef,
    slots: *mut c_void,
    traverse: *mut c_void,
    clear: *mut c_void,
    free: *mut c_void,
}

#[cfg_attr(windows, link(name = "python3", kind = "raw-dylib"))]
extern "C" {
    fn PyModule_Create2(def: *mut ModuleDef, api: c_int) -> *mut PyObj;
    fn PyLong_FromLongLong(v: i64) -> *mut PyObj;
    fn PyLong_AsLongLong(o: *mut PyObj) -> i64;
    fn PyFloat_AsDouble(o: *mut PyObj) -> f64;
    fn PyLong_AsUnsignedLongLong(o: *mut PyObj) -> u64;
    fn PyNumber_Index(o: *mut PyObj) -> *mut PyObj;
    fn PyObject_IsTrue(o: *mut PyObj) -> c_int;
    fn PyErr_Occurred() -> *mut PyObj;
    fn PyErr_Clear();
    fn PyEval_SaveThread() -> *mut c_void;
    fn PyEval_RestoreThread(t: *mut c_void);
    fn PyObject_CallFunctionObjArgs(callable: *mut PyObj, ...) -> *mut PyObj;
    fn PyTuple_New(n: isize) -> *mut PyObj;
    fn PyTuple_SetItem(t: *mut PyObj, i: isize, v: *mut PyObj) -> c_int;
    fn PyBool_FromLong(v: std::ffi::c_long) -> *mut PyObj;
    fn PyFloat_FromDouble(v: f64) -> *mut PyObj;
    fn Py_IncRef(o: *mut PyObj);
    fn Py_DecRef(o: *mut PyObj);
    fn PyType_IsSubtype(a: *mut c_void, b: *mut c_void) -> c_int;
}

// ---------------------------------------------------------------- loops that hand back new arrays

/// numpy.empty and each element kind's dtype (lib.rs K_* order), from native.py at load (setup_arrays).
static EMPTY: AtomicPtr<PyObj> = AtomicPtr::new(null_mut());
static DTYPES: [AtomicPtr<PyObj>; 11] = [const { AtomicPtr::new(null_mut()) }; 11];

unsafe extern "C" fn py_setup_arrays(_m: *mut PyObj, args: *const *mut PyObj, n: isize) -> *mut PyObj {
    if n != 12 {
        return PyLong_FromLongLong(crate::FAILED);
    }
    let a = std::slice::from_raw_parts(args, 12);
    for o in a {
        Py_IncRef(*o); // (kept for good)
    }
    EMPTY.store(a[0], Ordering::Release);
    for (k, d) in DTYPES.iter().enumerate() {
        d.store(a[k + 1], Ordering::Release);
    }
    PyLong_FromLongLong(0)
}

/// What a loop hands back, one item of the tuple: a new array (its items, kind, columns: 0 = 1-D), or a number.
#[allow(dead_code)]
pub enum Out {
    I64(Vec<i64>, usize),
    F64(Vec<f64>, usize),
    /// bytes as NumPy kind k (K_U8 or K_BOOL)
    U8(Vec<u8>, usize, usize),
    I32(Vec<i32>, usize),
    Int(i64),
    Float(f64),
    Bool(bool),
}
pub type Outs = Result<Vec<Out>, i64>;

/// A new NumPy array of kind k holding the bytes at src (n items of size sz; cols > 0 = 2-D, n / cols rows).
unsafe fn new_array(k: usize, src: *const u8, n: usize, sz: usize, cols: usize) -> *mut PyObj {
    let empty = EMPTY.load(Ordering::Acquire);
    let dt = DTYPES[k].load(Ordering::Acquire);
    if empty.is_null() || dt.is_null() {
        return null_mut();
    }
    let shape = if cols > 0 {
        let t = PyTuple_New(2);
        PyTuple_SetItem(t, 0, PyLong_FromLongLong((n / cols.max(1)) as i64));
        PyTuple_SetItem(t, 1, PyLong_FromLongLong(cols as i64));
        t
    } else {
        PyLong_FromLongLong(n as i64)
    };
    let arr = PyObject_CallFunctionObjArgs(empty, shape, dt, null_mut::<PyObj>());
    Py_DecRef(shape);
    if arr.is_null() {
        PyErr_Clear();
        return null_mut();
    }
    let a = &*(arr as *const Arr);
    if n * sz > 0 {
        std::ptr::copy_nonoverlapping(src, a.data, n * sz);
    }
    arr
}

/// The loop's result as a Python tuple (arrays made here, the GIL held), or an int below 0 (it didn't run).
unsafe fn to_tuple(r: Outs) -> *mut PyObj {
    let outs = match r {
        Ok(v) => v,
        Err(c) => return PyLong_FromLongLong(c),
    };
    let t = PyTuple_New(outs.len() as isize);
    for (i, o) in outs.into_iter().enumerate() {
        let v = match o {
            Out::I64(v, c) => new_array(crate::K_I64, v.as_ptr() as *const u8, v.len(), 8, c),
            Out::F64(v, c) => new_array(crate::K_F64, v.as_ptr() as *const u8, v.len(), 8, c),
            Out::U8(v, k, c) => new_array(k, v.as_ptr(), v.len(), 1, c),
            Out::I32(v, c) => new_array(crate::K_I32, v.as_ptr() as *const u8, v.len(), 4, c),
            Out::Int(v) => PyLong_FromLongLong(v),
            Out::Float(v) => PyFloat_FromDouble(v),
            Out::Bool(v) => PyBool_FromLong(v as std::ffi::c_long),
        };
        if v.is_null() {
            Py_DecRef(t);
            // (set up: numpy.empty / a number couldn't be made = no memory (Python's MemoryError, as NumPy's code))
            let ready = !EMPTY.load(Ordering::Acquire).is_null();
            return PyLong_FromLongLong(if ready { crate::NO_MEMORY } else { crate::FAILED });
        }
        PyTuple_SetItem(t, i as isize, v);
    }
    t
}

/// Runs f (a panic caught) with the GIL let go, its result made into Python values with it held again.
unsafe fn run_outs(f: impl FnOnce() -> Outs) -> *mut PyObj {
    let t = PyEval_SaveThread();
    let r = crate::guard_with(f);
    PyEval_RestoreThread(t);
    to_tuple(r)
}

unsafe fn len_of(o: *mut PyObj) -> isize {
    if !crate::is_array(o as *const Arr) {
        return 0;
    }
    let a = &*(o as *const Arr);
    if a.nd >= 1 { *a.dims } else { 0 }
}

/// How many items the biggest array among the arguments holds (a number / anything else: 0).
unsafe fn most_items(args: &[*mut PyObj]) -> isize {
    let mut most = 0isize;
    for &o in args {
        if crate::is_array(o as *const Arr) {
            let a = &*(o as *const Arr);
            let mut t: isize = if a.nd >= 1 { 1 } else { 0 };
            for k in 0..a.nd.max(0) as usize {
                t = t.saturating_mul((*a.dims.add(k)).max(0));
            }
            most = most.max(t);
        }
    }
    most
}

unsafe fn flag(o: *mut PyObj) -> i64 {
    let v = PyLong_AsLongLong(o);
    if v == -1 {
        PyErr_Clear();
    }
    v
}

/// Runs f with the GIL let go when big.
unsafe fn run(big: bool, f: impl FnOnce() -> i64) -> *mut PyObj {
    let r = if big {
        let t = PyEval_SaveThread();
        let r = f();
        PyEval_RestoreThread(t);
        r
    } else {
        f()
    };
    PyLong_FromLongLong(r)
}

unsafe extern "C" fn py_stir(_m: *mut PyObj, args: *const *mut PyObj, n: isize) -> *mut PyObj {
    if n != 7 {
        return PyLong_FromLongLong(crate::FAILED);
    }
    let a = std::slice::from_raw_parts(args, 7);
    let p = |i: usize| a[i] as *const Arr;
    let many = flag(a[6]);
    run(len_of(a[0]) >= GIL_FREE, || crate::sw_stir_spots(p(0), p(1), p(2), p(3), p(4), p(5), many))
}

unsafe extern "C" fn py_winner(_m: *mut PyObj, args: *const *mut PyObj, n: isize) -> *mut PyObj {
    if n != 8 {
        return PyLong_FromLongLong(crate::FAILED);
    }
    let a = std::slice::from_raw_parts(args, 8);
    let p = |i: usize| a[i] as *const Arr;
    run(len_of(a[4]) >= GIL_FREE, || crate::sw_winner_pieces(p(0), p(1), p(2), p(3), p(4), p(5), p(6), p(7)))
}

unsafe extern "C" fn py_key(_m: *mut PyObj, args: *const *mut PyObj, n: isize) -> *mut PyObj {
    if n != 6 {
        return PyLong_FromLongLong(crate::FAILED);
    }
    let a = std::slice::from_raw_parts(args, 6);
    let p = |i: usize| a[i] as *const Arr;
    let many = flag(a[5]);
    run(len_of(a[0]) >= GIL_FREE, || crate::sw_key_order(p(0), p(1), p(2), p(3), p(4), many))
}

// ---------------------------------------------------------------- the other loops: one line each (py_loops!)

/// One argument from Python: an array (Rust reads NumPy's struct: the wrong kind / spread out / read only ->
/// NEED_COPY, native.py makes plain copies of the kinds the doc names), a whole number, a number, a yes / no.
pub(crate) trait FromPy<'a>: Sized {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64>;
}
impl<'a, T: crate::Kind> FromPy<'a> for &'a [T] {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        crate::arr::<T>(o as *const Arr)
    }
}
impl<'a, T: crate::Kind> FromPy<'a> for &'a mut [T] {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        crate::arr_mut::<T>(o as *const Arr)
    }
}
impl<'a, T: crate::Kind, const N: usize> FromPy<'a> for crate::Nd<'a, T, N> {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        crate::nd::<T, N>(o as *const Arr)
    }
}
impl<'a, T: crate::Kind, const N: usize> FromPy<'a> for crate::NdMut<'a, T, N> {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        crate::nd_mut::<T, N>(o as *const Arr)
    }
}
impl<'a> FromPy<'a> for i64 {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        let v = PyLong_AsLongLong(o);
        if v == -1 && !PyErr_Occurred().is_null() {
            PyErr_Clear();
            return Err(NEED_COPY);
        }
        Ok(v)
    }
}
impl<'a> FromPy<'a> for u64 {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        let v = PyLong_AsUnsignedLongLong(o);
        if v == u64::MAX && !PyErr_Occurred().is_null() {
            PyErr_Clear();
            // (a NumPy whole number, e.g. np.uint64: PyLong_AsUnsignedLongLong takes Python ints only; its
            // __index__ gives one. Without this every such call took the slow plain-copy route: dice_mix 3.3 us)
            let i = PyNumber_Index(o);
            if i.is_null() {
                PyErr_Clear();
                return Err(NEED_COPY);
            }
            let v = PyLong_AsUnsignedLongLong(i);
            Py_DecRef(i);
            if v == u64::MAX && !PyErr_Occurred().is_null() {
                PyErr_Clear();
                return Err(NEED_COPY);
            }
            return Ok(v);
        }
        Ok(v)
    }
}
impl<'a> FromPy<'a> for f64 {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        let v = PyFloat_AsDouble(o);
        if v == -1.0 && !PyErr_Occurred().is_null() {
            PyErr_Clear();
            return Err(NEED_COPY);
        }
        Ok(v)
    }
}
impl<'a> FromPy<'a> for bool {
    unsafe fn from_py(o: *mut PyObj) -> Result<Self, i64> {
        let v = PyObject_IsTrue(o);
        if v < 0 {
            PyErr_Clear();
            return Err(NEED_COPY);
        }
        Ok(v != 0)
    }
}

/// Each loop: `name(arg: type, ...) => inner;` makes the Python function `name` (its doc = the argument types, read
/// by native.py) running inner at this processor's level, the GIL let go (the window keeps going; Numba's nogil
/// loops let it go too), a panic caught (FAILED). inner must return the i64 the Python side expects (>= 0).
/// `name(args) -> arrays => inner;`: inner returns Outs (new arrays / numbers), handed back as a tuple.
/// The GIL is let go only when the biggest array holds GIL_FREE items or more (small calls keep it: taking it back
/// can wait a few ms while another thread runs Python).
macro_rules! py_ret {
    (; $inner:path; $($a:ident: $t:ty),*) => {
        level_builds!(fn leveled($($a: $t),*) -> i64 => $inner);
        unsafe fn go(big: bool, $($a: $t),*) -> *mut PyObj {
            run(big, move || crate::guard(|| leveled($($a),*)))
        }
    };
    (arrays; $inner:path; $($a:ident: $t:ty),*) => {
        level_builds!(fn leveled($($a: $t),*) -> Outs => $inner);
        unsafe fn go(_big: bool, $($a: $t),*) -> *mut PyObj {
            run_outs(move || leveled($($a),*))
        }
    };
}

macro_rules! py_loops {
    (old: [$($old:expr),* $(,)?];
     $($name:ident($($a:ident: $t:ty),* $(,)?) $(-> $ret:ident)? => $inner:path;)*) => {
        #[allow(non_snake_case, unused_imports, clippy::too_many_arguments)]
        mod fns {
            $(
            pub mod $name {
                use super::super::*;
                use crate::{Flag, Nd, NdMut};
                py_ret!($($ret)?; $inner; $($a: $t),*);
                pub unsafe extern "C" fn call(_m: *mut PyObj, args: *const *mut PyObj, n: isize) -> *mut PyObj {
                    const WANT: usize = [$(stringify!($a)),*].len();
                    if n as usize != WANT {
                        return PyLong_FromLongLong(crate::FAILED);
                    }
                    let a = std::slice::from_raw_parts(args, WANT);
                    let mut k = 0usize;
                    $(
                        let $a: $t = match FromPy::from_py(a[k]) {
                            Ok(v) => v,
                            Err(c) => return PyLong_FromLongLong(c),
                        };
                        k += 1;
                    )*
                    let _ = k;
                    go(most_items(a) >= GIL_FREE, $($a),*)
                }
            }
            )*
        }
        static mut METHODS: [MethodDef; [$($old),*].len() + [$(stringify!($name)),*].len() + 2] = [
            $($old,)*
            MethodDef { name: c"setup_arrays".as_ptr(), meth: py_setup_arrays as *const c_void, flags: METH_FASTCALL,
                        doc: null() },
            $(MethodDef {
                name: concat!(stringify!($name), "\0").as_ptr() as *const c_char,
                meth: fns::$name::call as *const c_void,
                flags: METH_FASTCALL,
                doc: concat!($(stringify!($t), ";"),*, "\0").as_ptr() as *const c_char,
            },)*
            MethodDef { name: null(), meth: null(), flags: 0, doc: null() },
        ];
    };
}

const OLD_STIR: MethodDef =
    MethodDef { name: c"stir_spots".as_ptr(), meth: py_stir as *const c_void, flags: METH_FASTCALL, doc: null() };
const OLD_WINNER: MethodDef =
    MethodDef { name: c"winner_pieces".as_ptr(), meth: py_winner as *const c_void, flags: METH_FASTCALL, doc: null() };
const OLD_KEY: MethodDef =
    MethodDef { name: c"key_order".as_ptr(), meth: py_key as *const c_void, flags: METH_FASTCALL, doc: null() };

include!("loops_list.rs");

static mut DEF: ModuleDef = ModuleDef {
    base: DefBase { ob_base: Head { refcnt: 1, typ: null_mut() }, m_init: null(), m_index: 0, m_copy: null_mut() },
    name: c"spiderweb_loops".as_ptr(),
    doc: null(),
    size: -1,
    methods: null_mut(),
    slots: null_mut(),
    traverse: null_mut(),
    clear: null_mut(),
    free: null_mut(),
};

#[no_mangle]
pub unsafe extern "C" fn PyInit_spiderweb_loops() -> *mut PyObj {
    let d = addr_of_mut!(DEF);
    (*d).methods = addr_of_mut!(METHODS) as *mut MethodDef;
    crate::IS_SUB.store(PyType_IsSubtype as *mut c_void, Ordering::Relaxed); // (array subclasses: lib.rs is_array)
    PyModule_Create2(d, 3)
}
