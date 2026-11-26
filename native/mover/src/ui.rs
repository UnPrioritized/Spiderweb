//! The progress window: one line of text, a bar, Cancel (the X = Cancel too). Plain Windows controls. The work runs
//! on its own thread; the window shows what it reached ten times a second and closes when it ends.

use std::ffi::{c_int, c_void};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use crate::sys::wide;

type H = *mut c_void;

#[repr(C)]
struct WndClassExW {
    size: u32,
    style: u32,
    proc_: unsafe extern "system" fn(H, u32, usize, isize) -> isize,
    cls_extra: c_int,
    wnd_extra: c_int,
    instance: H,
    icon: H,
    cursor: H,
    background: H,
    menu_name: *const u16,
    class_name: *const u16,
    icon_small: H,
}

#[repr(C)]
struct Msg {
    hwnd: H,
    message: u32,
    wparam: usize,
    lparam: isize,
    time: u32,
    pt: [i32; 2],
    private: u32,
}

#[repr(C)]
struct InitCommonControlsEx {
    size: u32,
    icc: u32,
}

#[link(name = "user32")]
extern "system" {
    fn RegisterClassExW(c: *const WndClassExW) -> u16;
    fn CreateWindowExW(ex: u32, cls: *const u16, name: *const u16, style: u32, x: c_int, y: c_int, w: c_int,
                       h: c_int, parent: H, menu: H, inst: H, param: *mut c_void) -> H;
    fn DefWindowProcW(h: H, m: u32, w: usize, l: isize) -> isize;
    fn GetMessageW(m: *mut Msg, h: H, a: u32, b: u32) -> c_int;
    fn TranslateMessage(m: *const Msg) -> c_int;
    fn DispatchMessageW(m: *const Msg) -> isize;
    fn PostQuitMessage(code: c_int);
    fn SetTimer(h: H, id: usize, ms: u32, f: *const c_void) -> usize;
    fn SendMessageW(h: H, m: u32, w: usize, l: isize) -> isize;
    fn SetWindowTextW(h: H, t: *const u16) -> c_int;
    fn DestroyWindow(h: H) -> c_int;
    fn EnableWindow(h: H, on: c_int) -> c_int;
    fn LoadIconW(inst: H, name: *const u16) -> H;
    fn LoadCursorW(inst: H, name: *const u16) -> H;
    fn GetSystemMetrics(i: c_int) -> c_int;
    fn GetDpiForSystem() -> u32;
    fn AdjustWindowRectEx(r: *mut [i32; 4], style: u32, menu: c_int, ex: u32) -> c_int;
    fn IsDialogMessageW(h: H, m: *const Msg) -> c_int;
}
#[link(name = "kernel32")]
extern "system" {
    fn GetModuleHandleW(name: *const u16) -> H;
}
#[link(name = "gdi32")]
extern "system" {
    fn CreateFontW(h: c_int, w: c_int, esc: c_int, ori: c_int, weight: c_int, it: u32, un: u32, st: u32, cs: u32,
                   op: u32, cp: u32, q: u32, pf: u32, face: *const u16) -> H;
}
#[link(name = "comctl32")]
extern "system" {
    fn InitCommonControlsEx(i: *const InitCommonControlsEx) -> c_int;
}

/// What the work reached: done of all (all 0 = unknown; done u32::MAX = unpacking), shown by the window.
pub struct Shared {
    pub cancel: AtomicBool,
    pub finished: AtomicBool,
    pub done: AtomicU32,  // (in KB, or u32::MAX)
    pub all: AtomicU32,
    pub label: Mutex<String>,
}

static mut SHARED: Option<Arc<Shared>> = None;
static mut LABEL: H = std::ptr::null_mut();
static mut BAR: H = std::ptr::null_mut();
static mut BUTTON: H = std::ptr::null_mut();

unsafe extern "system" fn proc_(h: H, m: u32, w: usize, l: isize) -> isize {
    #[allow(static_mut_refs)]
    let s = SHARED.as_ref();
    match m {
        0x0010 => {  // WM_CLOSE = Cancel
            if let Some(s) = s {
                stop(s);
            }
            0
        }
        0x0111 if (w & 0xFFFF) == 2 => {  // WM_COMMAND from Cancel (id 2 = IDCANCEL: Esc too)
            if let Some(s) = s {
                stop(s);
            }
            0
        }
        0x0113 => {  // WM_TIMER
            if let Some(s) = s {
                if let Ok(t) = s.label.lock() {
                    SetWindowTextW(LABEL, wide(&t).as_ptr());
                }
                let (done, all) = (s.done.load(Ordering::Relaxed), s.all.load(Ordering::Relaxed));
                let pos = if done == u32::MAX { 1000 } else if all > 0 { (done as u64 * 1000 / all as u64) as usize } else { 0 };
                SendMessageW(BAR, 0x0402, pos, 0);  // PBM_SETPOS
                if s.finished.load(Ordering::Relaxed) {
                    DestroyWindow(h);
                }
            }
            0
        }
        0x0002 => {  // WM_DESTROY
            PostQuitMessage(0);
            0
        }
        _ => DefWindowProcW(h, m, w, l),
    }
}

unsafe fn stop(s: &Shared) {
    s.cancel.store(true, Ordering::Relaxed);
    EnableWindow(BUTTON, 0);
    if let Ok(mut t) = s.label.lock() {
        *t = crate::texts::get("updates.stopping", &[]);
    }
}

/// Shows the window while `work` runs on a thread; returns its result once it ended.
pub fn run<T: Send + 'static>(title: &str, shared: Arc<Shared>, work: impl FnOnce() -> T + Send + 'static) -> T {
    let s2 = shared.clone();
    let worker = std::thread::spawn(move || {
        let r = work();
        s2.finished.store(true, Ordering::Relaxed);
        r
    });
    unsafe {
        SHARED = Some(shared.clone());
        let icc = InitCommonControlsEx { size: 8, icc: 0x20 | 0x4000 };  // (progress bar + standard classes)
        InitCommonControlsEx(&icc);
        let inst = GetModuleHandleW(std::ptr::null());
        let cls = wide("SpiderwebMover");
        let wc = WndClassExW {
            size: std::mem::size_of::<WndClassExW>() as u32, style: 0, proc_, cls_extra: 0, wnd_extra: 0,
            instance: inst, icon: LoadIconW(inst, 1 as *const u16), cursor: LoadCursorW(std::ptr::null_mut(), 32512 as *const u16),
            background: 16 as H, menu_name: std::ptr::null(), class_name: cls.as_ptr(), icon_small: std::ptr::null_mut(),
        };
        RegisterClassExW(&wc);
        let dpi = GetDpiForSystem().max(96) as i32;
        let px = |v: i32| v * dpi / 96;
        let style = 0x00C0_0000 | 0x0008_0000 | 0x1000_0000;  // (caption, system menu, visible)
        let mut r = [0, 0, px(440), px(118)];
        AdjustWindowRectEx(&mut r, style, 0, 0);
        let (w, h) = (r[2] - r[0], r[3] - r[1]);
        let (sw, sh) = (GetSystemMetrics(0), GetSystemMetrics(1));
        let win = CreateWindowExW(0, cls.as_ptr(), wide(title).as_ptr(), style, (sw - w) / 2, (sh - h) / 2, w, h,
                                  std::ptr::null_mut(), std::ptr::null_mut(), inst, std::ptr::null_mut());
        let font = CreateFontW(-px(12), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, wide("Segoe UI").as_ptr());
        let child = 0x4000_0000 | 0x1000_0000;
        LABEL = CreateWindowExW(0, wide("STATIC").as_ptr(), wide(&shared.label.lock().map(|t| t.clone()).unwrap_or_default()).as_ptr(),
                                child, px(16), px(16), px(408), px(20), win, std::ptr::null_mut(), inst, std::ptr::null_mut());
        BAR = CreateWindowExW(0, wide("msctls_progress32").as_ptr(), std::ptr::null(), child, px(16), px(44), px(408),
                              px(18), win, std::ptr::null_mut(), inst, std::ptr::null_mut());
        SendMessageW(BAR, 0x0406, 0, 1000);  // PBM_SETRANGE32 0..1000
        BUTTON = CreateWindowExW(0, wide("BUTTON").as_ptr(), wide(&crate::texts::get("mover.cancel", &[])).as_ptr(),
                                 child | 0x0001_0000, px(336), px(76), px(88), px(28), win, 2 as H, inst,
                                 std::ptr::null_mut());
        for c in [LABEL, BUTTON] {
            SendMessageW(c, 0x0030, font as usize, 1);  // WM_SETFONT
        }
        SetTimer(win, 1, 100, std::ptr::null());
        let mut msg: Msg = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            if IsDialogMessageW(win, &msg) == 0 {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }
    match worker.join() {
        Ok(r) => r,
        Err(p) => std::panic::resume_unwind(p),
    }
}
