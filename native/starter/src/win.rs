//! The few Windows calls the starter makes, declared by hand (no crates).

use std::ffi::{c_int, c_void, OsStr};
use std::os::windows::ffi::OsStrExt;

#[link(name = "kernel32")]
extern "system" {
    fn LoadLibraryExW(name: *const u16, file: *mut c_void, flags: u32) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
}
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(hwnd: *mut c_void, text: *const u16, caption: *const u16, kind: u32) -> c_int;
    fn AllowSetForegroundWindow(pid: u32) -> c_int;
    fn EnumWindows(cb: extern "system" fn(*mut c_void, isize) -> c_int, data: isize) -> c_int;
    fn GetWindowThreadProcessId(hwnd: *mut c_void, pid: *mut u32) -> u32;
    fn IsWindowVisible(hwnd: *mut c_void) -> c_int;
}

#[link(name = "kernel32")]
extern "system" {
    fn OpenMutexW(access: u32, inherit: c_int, name: *const u16) -> *mut c_void;
    fn CloseHandle(h: *mut c_void) -> c_int;
}

/// Whether some program holds the named lock `name` now.
pub fn lock_taken(name: &str) -> bool {
    let n = wide(OsStr::new(name));
    let h = unsafe { OpenMutexW(0x0010_0000, 0, n.as_ptr()) };  // (SYNCHRONIZE)
    if h.is_null() {
        return false;
    }
    unsafe { CloseHandle(h) };
    true
}

/// A message with OK.
pub fn tell(text: &str) {
    sharp();
    let t = wide(OsStr::new(text));
    let c = wide(OsStr::new("Spiderweb"));
    unsafe { MessageBoxW(std::ptr::null_mut(), t.as_ptr(), c.as_ptr(), 0x40 | MB_SETFOREGROUND) };
}

/// Lets the process `pid` (Spiderweb, started by this starter) bring its window to the front: Windows only lets a
/// program started by the one in front do that, and that is this starter, not Spiderweb.
pub fn allow_front(pid: u32) {
    unsafe { AllowSetForegroundWindow(pid) };
}

/// Whether the process `pid` shows a window.
pub fn shows_window(pid: u32) -> bool {
    extern "system" fn look(hwnd: *mut c_void, data: isize) -> c_int {
        let seek = unsafe { &mut *(data as *mut (u32, bool)) };
        let mut owner = 0u32;
        unsafe { GetWindowThreadProcessId(hwnd, &mut owner) };
        if owner == seek.0 && unsafe { IsWindowVisible(hwnd) } != 0 {
            seek.1 = true;
            return 0;
        }
        1
    }
    let mut seek = (pid, false);
    unsafe { EnumWindows(look, &mut seek as *mut (u32, bool) as isize) };
    seek.1
}
#[link(name = "shell32")]
extern "system" {
    fn ShellExecuteW(hwnd: *mut c_void, op: *const u16, file: *const u16, params: *const u16, dir: *const u16,
                     show: c_int) -> *mut c_void;
}

const LOAD_WITH_ALTERED_SEARCH_PATH: u32 = 0x0000_0008;
const MB_YESNO: u32 = 0x04;
const MB_ICONWARNING: u32 = 0x30;
const MB_SETFOREGROUND: u32 = 0x1_0000;
const IDYES: c_int = 6;

pub fn wide(s: &OsStr) -> Vec<u16> {
    s.encode_wide().chain(std::iter::once(0)).collect()
}

fn sharp() {
    // (a message box drawn sharp on scaled screens; the program asks for the same mode itself, so nothing changes)
    #[link(name = "shcore")]
    extern "system" {
        fn SetProcessDpiAwareness(value: c_int) -> i32;
    }
    unsafe { SetProcessDpiAwareness(1) };
}

/// A question with Yes / No: true for Yes.
pub fn ask(text: &str) -> bool {
    sharp();
    let t = wide(OsStr::new(text));
    let c = wide(OsStr::new("Spiderweb"));
    unsafe { MessageBoxW(std::ptr::null_mut(), t.as_ptr(), c.as_ptr(), MB_YESNO | MB_ICONWARNING | MB_SETFOREGROUND) == IDYES }
}

/// Opens a web page in the user's browser.
pub fn open_page(url: &str) {
    let op = wide(OsStr::new("open"));
    let u = wide(OsStr::new(url));
    unsafe { ShellExecuteW(std::ptr::null_mut(), op.as_ptr(), u.as_ptr(), std::ptr::null(), std::ptr::null(), 1) };
}

/// Python's Py_Main from the DLL at `path` (its own folder searched first for what it needs), or None.
pub fn py_main(path: &std::path::Path) -> Option<extern "C" fn(c_int, *mut *mut u16) -> c_int> {
    let w = wide(path.as_os_str());
    let lib = unsafe { LoadLibraryExW(w.as_ptr(), std::ptr::null_mut(), LOAD_WITH_ALTERED_SEARCH_PATH) };
    if lib.is_null() {
        return None;
    }
    let f = unsafe { GetProcAddress(lib, b"Py_Main\0".as_ptr()) };
    if f.is_null() {
        return None;
    }
    Some(unsafe { std::mem::transmute::<*mut c_void, extern "C" fn(c_int, *mut *mut u16) -> c_int>(f) })
}
