//! Windows' own pieces, declared by hand: WinHTTP (downloads, https; follows GitHub's redirects), BCrypt (SHA-256,
//! random names), message boxes, opening a web page.

use std::ffi::{c_int, c_void, OsStr};
use std::os::windows::ffi::OsStrExt;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::Fail;

pub fn wide(s: &str) -> Vec<u16> {
    OsStr::new(s).encode_wide().chain(std::iter::once(0)).collect()
}

type H = *mut c_void;

#[link(name = "winhttp")]
extern "system" {
    fn WinHttpOpen(agent: *const u16, access: u32, proxy: *const u16, bypass: *const u16, flags: u32) -> H;
    fn WinHttpSetTimeouts(h: H, resolve: c_int, connect: c_int, send: c_int, receive: c_int) -> c_int;
    fn WinHttpConnect(s: H, host: *const u16, port: u16, reserved: u32) -> H;
    fn WinHttpOpenRequest(c: H, verb: *const u16, path: *const u16, version: *const u16, referrer: *const u16,
                          accept: *const *const u16, flags: u32) -> H;
    fn WinHttpSendRequest(r: H, headers: *const u16, hlen: u32, opt: *const c_void, olen: u32, total: u32,
                          ctx: usize) -> c_int;
    fn WinHttpReceiveResponse(r: H, reserved: *mut c_void) -> c_int;
    fn WinHttpQueryHeaders(r: H, level: u32, name: *const u16, buf: *mut c_void, len: *mut u32, index: *mut u32)
        -> c_int;
    fn WinHttpReadData(r: H, buf: *mut c_void, n: u32, read: *mut u32) -> c_int;
    fn WinHttpCloseHandle(h: H) -> c_int;
}
#[link(name = "bcrypt")]
extern "system" {
    fn BCryptOpenAlgorithmProvider(h: *mut H, alg: *const u16, imp: *const u16, flags: u32) -> i32;
    fn BCryptCreateHash(alg: H, h: *mut H, obj: *mut u8, olen: u32, secret: *const u8, slen: u32, flags: u32) -> i32;
    fn BCryptHashData(h: H, data: *const u8, len: u32, flags: u32) -> i32;
    fn BCryptFinishHash(h: H, out: *mut u8, len: u32, flags: u32) -> i32;
    fn BCryptDestroyHash(h: H) -> i32;
    fn BCryptCloseAlgorithmProvider(h: H, flags: u32) -> i32;
    fn BCryptGenRandom(alg: H, buf: *mut u8, len: u32, flags: u32) -> i32;
}
#[link(name = "user32")]
extern "system" {
    fn MessageBoxW(hwnd: H, text: *const u16, caption: *const u16, kind: u32) -> c_int;
}
#[link(name = "shell32")]
extern "system" {
    fn ShellExecuteW(hwnd: H, op: *const u16, file: *const u16, params: *const u16, dir: *const u16, show: c_int)
        -> H;
}

struct Handle(H);
impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe { WinHttpCloseHandle(self.0) };
        }
    }
}

/// Downloads url: each block handed to `sink`, `progress(done, size)` (size 0 = unknown). More than `most`
/// bytes = too_big; `cancel` set = cancelled; network trouble or a refusal (not 200) = no_connection.
pub fn download(url: &str, most: u64, cancel: &AtomicBool, sink: &mut dyn FnMut(&[u8]) -> Result<(), Fail>,
                progress: &mut dyn FnMut(u64, u64)) -> Result<(), Fail> {
    let (secure, rest) = if let Some(r) = url.strip_prefix("https://") {
        (true, r)
    } else if let Some(r) = url.strip_prefix("http://") {
        (false, r)
    } else {
        return Err(Fail::NoConnection);
    };
    let (host_port, path) = rest.split_once('/').map(|(h, p)| (h, format!("/{p}"))).unwrap_or((rest, "/".into()));
    let (host, port) = match host_port.rsplit_once(':') {
        Some((h, p)) => (h, p.parse::<u16>().map_err(|_| Fail::NoConnection)?),
        None => (host_port, if secure { 443 } else { 80 }),
    };
    let agent = wide(&format!("Spiderweb-mover/{}", crate::VERSION));
    unsafe {
        // (automatic proxy first: Windows 8.1+; else the system's)
        let mut s = WinHttpOpen(agent.as_ptr(), 4, std::ptr::null(), std::ptr::null(), 0);
        if s.is_null() {
            s = WinHttpOpen(agent.as_ptr(), 0, std::ptr::null(), std::ptr::null(), 0);
        }
        let s = Handle(s);
        if s.0.is_null() {
            return Err(Fail::NoConnection);
        }
        WinHttpSetTimeouts(s.0, 30_000, 30_000, 30_000, 60_000);
        let c = Handle(WinHttpConnect(s.0, wide(host).as_ptr(), port, 0));
        if c.0.is_null() {
            return Err(Fail::NoConnection);
        }
        let r = Handle(WinHttpOpenRequest(c.0, wide("GET").as_ptr(), wide(&path).as_ptr(), std::ptr::null(),
                                          std::ptr::null(), std::ptr::null(), if secure { 0x0080_0000 } else { 0 }));
        if r.0.is_null() || WinHttpSendRequest(r.0, std::ptr::null(), 0, std::ptr::null(), 0, 0, 0) == 0
            || WinHttpReceiveResponse(r.0, std::ptr::null_mut()) == 0 {
            return Err(Fail::NoConnection);
        }
        let number = |what: u32| -> u32 {
            let (mut v, mut n) = (0u32, 4u32);
            let ok = WinHttpQueryHeaders(r.0, what | 0x2000_0000, std::ptr::null(), &mut v as *mut u32 as *mut c_void,
                                         &mut n, std::ptr::null_mut());
            if ok == 0 { 0 } else { v }
        };
        if number(19) != 200 {  // (WINHTTP_QUERY_STATUS_CODE)
            return Err(Fail::NoConnection);
        }
        let size = number(5) as u64;  // (WINHTTP_QUERY_CONTENT_LENGTH; 0 = not said)
        if size > most {
            return Err(Fail::TooBig);
        }
        let mut buf = vec![0u8; 1 << 16];
        let mut done = 0u64;
        loop {
            if cancel.load(Ordering::Relaxed) {
                return Err(Fail::Cancelled);
            }
            let mut got = 0u32;
            if WinHttpReadData(r.0, buf.as_mut_ptr() as *mut c_void, buf.len() as u32, &mut got) == 0 {
                return Err(Fail::NoConnection);
            }
            if got == 0 {
                break;
            }
            done += got as u64;
            if done > most {
                return Err(Fail::TooBig);
            }
            sink(&buf[..got as usize])?;
            progress(done, size);
        }
        if size != 0 && done != size {
            return Err(Fail::NoConnection);  // (cut off)
        }
    }
    Ok(())
}

/// SHA-256, fed in pieces (Windows' own).
pub struct Sha256 {
    alg: H,
    h: H,
}

impl Sha256 {
    pub fn new() -> Option<Sha256> {
        let (mut alg, mut h): (H, H) = (std::ptr::null_mut(), std::ptr::null_mut());
        unsafe {
            if BCryptOpenAlgorithmProvider(&mut alg, wide("SHA256").as_ptr(), std::ptr::null(), 0) != 0 {
                return None;
            }
            if BCryptCreateHash(alg, &mut h, std::ptr::null_mut(), 0, std::ptr::null(), 0, 0) != 0 {
                BCryptCloseAlgorithmProvider(alg, 0);
                return None;
            }
        }
        Some(Sha256 { alg, h })
    }

    pub fn update(&mut self, data: &[u8]) {
        for piece in data.chunks(1 << 30) {
            unsafe { BCryptHashData(self.h, piece.as_ptr(), piece.len() as u32, 0) };
        }
    }

    /// Lowercase hex.
    pub fn hex(self) -> String {
        let mut out = [0u8; 32];
        unsafe { BCryptFinishHash(self.h, out.as_mut_ptr(), 32, 0) };
        out.iter().map(|b| format!("{b:02x}")).collect()
    }
}

impl Drop for Sha256 {
    fn drop(&mut self) {
        unsafe {
            BCryptDestroyHash(self.h);
            BCryptCloseAlgorithmProvider(self.alg, 0);
        }
    }
}

/// 8 random hex letters (names of our own temporary files / folders).
pub fn random_hex() -> String {
    let mut b = [0u8; 4];
    let ok = unsafe { BCryptGenRandom(std::ptr::null_mut(), b.as_mut_ptr(), 4, 2) } == 0;  // (system RNG)
    if !ok {
        let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        b = ((t as u32) ^ std::process::id()).to_le_bytes();
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn sharp() {
    #[link(name = "shcore")]
    extern "system" {
        fn SetProcessDpiAwareness(value: c_int) -> i32;
    }
    unsafe { SetProcessDpiAwareness(1) };
}

fn boxed(text: &str, kind: u32) -> c_int {
    sharp();
    let t = wide(text);
    let c = wide(&crate::texts::get("mover.title", &[("version", crate::VERSION)]));
    unsafe { MessageBoxW(std::ptr::null_mut(), t.as_ptr(), c.as_ptr(), kind | 0x1_0000) }
}

/// Yes / No (warning sign): true = Yes.
pub fn ask(text: &str) -> bool {
    boxed(text, 0x04 | 0x30) == 6
}

/// OK only (information or error sign).
pub fn tell(text: &str, error: bool) {
    boxed(text, if error { 0x10 } else { 0x40 });
}

pub fn open_page(url: &str) {
    unsafe {
        ShellExecuteW(std::ptr::null_mut(), wide("open").as_ptr(), wide(url).as_ptr(), std::ptr::null(),
                      std::ptr::null(), 1)
    };
}
