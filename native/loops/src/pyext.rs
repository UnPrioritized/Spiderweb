//! The same loops as a Python extension module (stable ABI 3, METH_FASTCALL; scripts/files/native.py load_ext loads
//! this very file as one): a tiny call ~0.4 us, where ctypes' own floor is ~1 us and Numba's ~0.8 us (PLAN.md B:
//! never slower than Numba). The file stays a ctypes library too (sw_* functions: used when this can't load).
//! Same arguments as the sw_* functions; they're called as they are. The GIL is let go only for big inputs
//! (GIL_FREE items: letting go + taking it back costs ~0.1 us; notes are made on a thread mid-drag, so the window
//! must keep running through long calls). python3.dll is linked by name (raw-dylib: no import library needed).

use crate::Arr;
use std::ffi::{c_char, c_int, c_void};
use std::ptr::{addr_of_mut, null, null_mut};

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
    fn PyErr_Clear();
    fn PyEval_SaveThread() -> *mut c_void;
    fn PyEval_RestoreThread(t: *mut c_void);
}

unsafe fn len_of(o: *mut PyObj) -> isize {
    let a = &*(o as *const Arr);
    if a.nd >= 1 { *a.dims } else { 0 }
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

static mut METHODS: [MethodDef; 4] = [
    MethodDef { name: c"stir_spots".as_ptr(), meth: py_stir as *const c_void, flags: METH_FASTCALL, doc: null() },
    MethodDef { name: c"winner_pieces".as_ptr(), meth: py_winner as *const c_void, flags: METH_FASTCALL, doc: null() },
    MethodDef { name: c"key_order".as_ptr(), meth: py_key as *const c_void, flags: METH_FASTCALL, doc: null() },
    MethodDef { name: null(), meth: null(), flags: 0, doc: null() },
];

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
    PyModule_Create2(d, 3)
}
