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

// Windows' task dialog (comctl32 6, which starter.manifest asks for): a message with buttons that say what they do.
// Declared with 1-byte packing, as commctrl.h does.
#[repr(C, packed(1))]
struct TaskButton {
    id: c_int,
    text: *const u16,
}
#[repr(C, packed(1))]
struct TaskConfig {
    size: u32,
    parent: *mut c_void,
    instance: *mut c_void,
    flags: u32,
    common_buttons: u32,
    title: *const u16,
    main_icon: *const u16,
    main_text: *const u16,
    content: *const u16,
    n_buttons: u32,
    buttons: *const TaskButton,
    default_button: c_int,
    n_radios: u32,
    radios: *const TaskButton,
    default_radio: c_int,
    verification: *const u16,
    expanded: *const u16,
    expanded_control: *const u16,
    collapsed_control: *const u16,
    footer_icon: *const u16,
    footer: *const u16,
    callback: Option<extern "system" fn(*mut c_void, u32, usize, isize, isize) -> i32>,
    callback_data: isize,
    width: u32,
}
type TaskDialogIndirect = extern "system" fn(*const TaskConfig, *mut c_int, *mut c_int, *mut c_int) -> i32;
const TDF_ALLOW_DIALOG_CANCELLATION: u32 = 0x08;
const TDF_USE_COMMAND_LINKS: u32 = 0x10;
const TDN_CREATED: u32 = 0;
const IDCANCEL: c_int = 2;
const FIRST_ID: c_int = 100;
const LOAD_LIBRARY_SEARCH_SYSTEM32: u32 = 0x0800;
const MB_YESNOCANCEL: u32 = 0x03;
const IDNO: c_int = 7;

#[link(name = "user32")]
extern "system" {
    fn SetForegroundWindow(hwnd: *mut c_void) -> c_int;
}

extern "system" fn task_events(hwnd: *mut c_void, note: u32, _w: usize, _l: isize, _data: isize) -> i32 {
    if note == TDN_CREATED {
        unsafe { SetForegroundWindow(hwnd) };  // (as the Yes / No boxes' MB_SETFOREGROUND)
    }
    0
}

/// A message with one button per choice (a choice's text after "\n" = a smaller line under it): the index of the
/// one picked. The LAST choice is what Esc / X give (Close). Without the task dialog (a build without the manifest):
/// a Yes / No (/ Cancel) box naming what each button does.
pub fn choose(text: &str, choices: &[String]) -> usize {
    sharp();
    let last = choices.len().saturating_sub(1);
    let name = wide(OsStr::new("comctl32.dll"));
    let lib = unsafe { LoadLibraryExW(name.as_ptr(), std::ptr::null_mut(), LOAD_LIBRARY_SEARCH_SYSTEM32) };
    let f = if lib.is_null() { std::ptr::null_mut() } else {
        unsafe { GetProcAddress(lib, b"TaskDialogIndirect\0".as_ptr()) }
    };
    if !f.is_null() && (2..=3).contains(&choices.len()) {
        let dialog = unsafe { std::mem::transmute::<*mut c_void, TaskDialogIndirect>(f) };
        let texts: Vec<Vec<u16>> = choices.iter().map(|c| wide(OsStr::new(c))).collect();
        let buttons: Vec<TaskButton> = texts.iter().enumerate()
            .map(|(i, t)| TaskButton { id: if i == last { IDCANCEL } else { FIRST_ID + i as c_int }, text: t.as_ptr() })
            .collect();
        let title = wide(OsStr::new("Spiderweb"));
        let content = wide(OsStr::new(text));
        let config = TaskConfig {
            size: std::mem::size_of::<TaskConfig>() as u32,
            parent: std::ptr::null_mut(),
            instance: std::ptr::null_mut(),
            flags: TDF_ALLOW_DIALOG_CANCELLATION | TDF_USE_COMMAND_LINKS,
            common_buttons: 0,
            title: title.as_ptr(),
            main_icon: 0xFFFF as *const u16,  // (TD_WARNING_ICON)
            main_text: std::ptr::null(),
            content: content.as_ptr(),
            n_buttons: buttons.len() as u32,
            buttons: buttons.as_ptr(),
            default_button: FIRST_ID,
            n_radios: 0,
            radios: std::ptr::null(),
            default_radio: 0,
            verification: std::ptr::null(),
            expanded: std::ptr::null(),
            expanded_control: std::ptr::null(),
            collapsed_control: std::ptr::null(),
            footer_icon: std::ptr::null(),
            footer: std::ptr::null(),
            callback: Some(task_events),
            callback_data: 0,
            width: 0,
        };
        let mut picked: c_int = 0;
        if dialog(&config, &mut picked, std::ptr::null_mut(), std::ptr::null_mut()) == 0 {
            return if picked >= FIRST_ID && ((picked - FIRST_ID) as usize) < last { (picked - FIRST_ID) as usize }
                   else { last };
        }
    }
    // (the plain box: "Yes = ..., No = ..., Cancel = ...", each choice's first line)
    let words = ["Yes", "No", "Cancel"];
    let mut full = text.to_string() + "\n";
    for (i, c) in choices.iter().take(3).enumerate() {
        full += &format!("\n{} = {}", words[i], c.lines().next().unwrap_or(""));
    }
    let t = wide(OsStr::new(&full));
    let c = wide(OsStr::new("Spiderweb"));
    let kind = if choices.len() >= 3 { MB_YESNOCANCEL } else { MB_YESNO };
    let got = unsafe { MessageBoxW(std::ptr::null_mut(), t.as_ptr(), c.as_ptr(), kind | MB_ICONWARNING | MB_SETFOREGROUND) };
    match got {
        IDYES => 0,
        IDNO if choices.len() >= 3 => 1,
        _ => last,
    }
}

/// Opens a file in its usual program (a .log: the text editor); Notepad when nothing is set up for it.
pub fn open_file(path: &std::path::Path) {
    let op = wide(OsStr::new("open"));
    let p = wide(path.as_os_str());
    let done = unsafe { ShellExecuteW(std::ptr::null_mut(), op.as_ptr(), p.as_ptr(), std::ptr::null(), std::ptr::null(), 1) };
    if (done as isize) <= 32 {
        let _ = std::process::Command::new("notepad.exe").arg(path).spawn();
    }
}

#[repr(C)]
struct SystemTime {
    year: u16, month: u16, weekday: u16, day: u16, hour: u16, minute: u16, second: u16, ms: u16,
}
#[link(name = "kernel32")]
extern "system" {
    fn GetLocalTime(t: *mut SystemTime);
}

/// The local date and time as "YYYYMMDD-HHMMSS" (files/project.py keep_aside's names).
pub fn stamp() -> String {
    let mut t = SystemTime { year: 0, month: 0, weekday: 0, day: 0, hour: 0, minute: 0, second: 0, ms: 0 };
    unsafe { GetLocalTime(&mut t) };
    format!("{:04}{:02}{:02}-{:02}{:02}{:02}", t.year, t.month, t.day, t.hour, t.minute, t.second)
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
