//! The mover: the small Spiderweb.exe on each release page. Old one-file versions (1.7.x) only know how to fetch
//! "Spiderweb.exe": their updater swaps this in (the old exe kept as Spiderweb.exe.old) and starts it with
//! --updated. It then sets up the folder version in the same folder: downloads Spiderweb-<version>.zip + the signed
//! list from this release, checks the signature (our public key, built in) and the zip's SHA-256, unpacks
//! data\<version>\ (folder.rs: own folder until whole), writes data\version.txt, puts the starter in its own place
//! and starts it. user\ and output\ are never touched. Anything failing = the old exe put back and started again,
//! with a message (never a folder without a working Spiderweb).
//! Downloaded by hand: the same where it is (a small installer); in a folder holding other things it first asks to
//! use a new "Spiderweb" folder there (user 2026-11-26); a "data" folder that isn't Spiderweb's is never unpacked
//! into (a new "Spiderweb" folder then, said). SPIDERWEB_MOVER_FROM = another download place (tests).
#![windows_subsystem = "windows"]

mod folder;
mod sys;
mod texts;
mod ui;

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const VERSION: &str = env!("SPIDERWEB_VERSION");
const PUBLIC_KEY: &str = env!("SPIDERWEB_PUBLIC_KEY");  // (base64, from scripts/files/signing.py)
const SITE: &str = "https://github.com/UnPrioritized/Spiderweb";
const UPDATED: &str = "--updated";
const REMOVE: &str = "--remove-original";
const OURS: [&str; 11] = ["spiderweb.exe", "spiderweb.exe.old", "readme.txt", "license", "third-party-notices.txt",
                          "spiderweb-notes-format.md", "data", "user", "output", "lang", "desktop.ini"];

/// Why it can't go on: the end of an "updates.fail.*" text key.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Fail {
    NoConnection,
    TooBig,
    Cancelled,
    Disk,
    BadFile,
    BadSignature,
    WrongVersion,
    NewerList,
    StartFailed,
    Other,
}

impl Fail {
    fn key(self) -> &'static str {
        match self {
            Fail::NoConnection => "no_connection",
            Fail::TooBig => "too_big",
            Fail::Cancelled => "cancelled",
            Fail::Disk => "disk",
            Fail::BadFile => "bad_file",
            Fail::BadSignature => "bad_signature",
            Fail::WrongVersion => "wrong_version",
            Fail::NewerList => "newer_list",
            Fail::StartFailed => "start_failed",
            Fail::Other => "other",
        }
    }
}

fn from() -> String {
    std::env::var("SPIDERWEB_MOVER_FROM").unwrap_or_else(|_| format!("{SITE}/releases/download/v{VERSION}/"))
}

fn zip_name() -> String {
    format!("Spiderweb-{VERSION}.zip")
}

fn b64(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0);
    for c in s.trim().bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            _ => return None,
        } as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
        }
    }
    Some(out)
}

/// The signed list's (version, {name: sha256}): Err when not signed by our key or not readable here.
fn checked_list(list: &[u8], sig: &[u8]) -> Result<(String, Vec<(String, String)>), Fail> {
    let key = b64(PUBLIC_KEY).and_then(|k| ed25519_compact::PublicKey::from_slice(&k).ok()).ok_or(Fail::BadSignature)?;
    let sig = std::str::from_utf8(sig).ok().and_then(b64)
        .and_then(|s| ed25519_compact::Signature::from_slice(&s).ok()).ok_or(Fail::BadSignature)?;
    key.verify(list, &sig).map_err(|_| Fail::BadSignature)?;
    let text = std::str::from_utf8(list).map_err(|_| Fail::NewerList)?;
    let mut lines = text.split('\n');
    if lines.next() != Some("spiderweb-update 1") {
        return Err(Fail::NewerList);
    }
    let (mut version, mut hashes) = (None, Vec::new());
    for l in lines {
        let p: Vec<&str> = l.splitn(3, ' ').collect();
        match p.as_slice() {
            ["version", v] if version.is_none() => version = Some(v.to_string()),
            ["sha256", h, n] if h.len() == 64 => hashes.push((n.to_string(), h.to_ascii_lowercase())),
            _ => {}  // (line kinds a later version may add: skipped, still signed)
        }
    }
    Ok((version.ok_or(Fail::NewerList)?, hashes))
}

fn fetch_small(url: &str, cancel: &AtomicBool) -> Result<Vec<u8>, Fail> {
    let mut got = Vec::new();
    sys::download(url, 64 * 1024, cancel, &mut |b| { got.extend_from_slice(b); Ok(()) }, &mut |_, _| {})?;
    Ok(got)
}

/// Download, check, unpack: data\<version>\, data\version.txt, <exe>.new = the starter.
fn set_up(top: &Path, exe: &Path, s: &ui::Shared) -> Result<PathBuf, Fail> {
    let say = |key: &str, places: &[(&str, &str)]| {
        if let Ok(mut t) = s.label.lock() {
            if !s.cancel.load(Ordering::Relaxed) {
                *t = texts::get(key, places);
            }
        }
    };
    let data = top.join("data");
    fs::create_dir_all(&data).map_err(|_| Fail::Disk)?;
    let base = from();
    let list = fetch_small(&format!("{base}Spiderweb-update.txt"), &s.cancel)?;
    let sig = fetch_small(&format!("{base}Spiderweb-update.sig"), &s.cancel)?;
    let (version, hashes) = checked_list(&list, &sig)?;
    let name = zip_name();
    let want = hashes.iter().find(|(n, _)| *n == name).map(|(_, h)| h.clone());
    let (true, Some(want)) = (version == VERSION, want) else { return Err(Fail::WrongVersion) };
    let mut zip = Vec::new();
    let mut sha = sys::Sha256::new().ok_or(Fail::Other)?;
    sys::download(&format!("{base}{name}"), 1 << 30, &s.cancel, &mut |b| {
        sha.update(b);
        zip.extend_from_slice(b);
        Ok(())
    }, &mut |done, all| {
        s.done.store((done / 1024) as u32, Ordering::Relaxed);
        s.all.store((all / 1024) as u32, Ordering::Relaxed);
        let mb = |n: u64| format!("{:.1}", n as f64 / 1048576.0);
        say("mover.downloading", &[("version", VERSION), ("done", &mb(done)), ("size", &mb(all))]);
    })?;
    if sha.hex() != want {
        return Err(Fail::BadFile);
    }
    s.done.store(u32::MAX, Ordering::Relaxed);
    say("mover.unpacking", &[("version", VERSION)]);
    let new_exe = PathBuf::from(format!("{}.new", exe.display()));
    folder::unpack(&zip, VERSION, &data, &new_exe, top, &s.cancel, &mut |_, _| {})?;
    let tmp = data.join(format!("version.txt.{}", sys::random_hex()));
    fs::write(&tmp, format!("{VERSION}\n")).and_then(|_| fs::rename(&tmp, data.join("version.txt"))).map_err(|_| {
        let _ = fs::remove_file(&tmp);
        Fail::Disk
    })?;
    Ok(new_exe)
}

fn retry(secs: u64, mut job: impl FnMut() -> std::io::Result<()>) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        if job().is_ok() {
            return true;
        }
        if Instant::now() > end {
            return false;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}

fn start(exe: &Path, args: &[&str]) -> bool {
    std::process::Command::new(exe).args(args).current_dir(exe.parent().unwrap_or(Path::new("."))).spawn().is_ok()
}

fn log_error(top: &Path, what: &str) {
    use std::io::Write;
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(top.join("spiderweb-error.log")) {
        let _ = writeln!(f, "==== Spiderweb {VERSION} mover\n{what}");
    }
}

fn plus(p: &Path, end: &str) -> PathBuf {
    PathBuf::from(format!("{}{end}", p.display()))
}

/// Nothing of the new version stays in use: the old exe back (and started) when there is one; a message.
fn give_up(exe: &Path, updating: bool, why: Fail) -> i32 {
    let text = texts::get(&format!("updates.fail.{}", why.key()), &[]);
    let old = plus(exe, ".old");
    if updating && old.is_file() {
        let gone = plus(exe, ".new-mover");  // (an old version deletes "<exe>.new*" at its start)
        if retry(10, || fs::rename(exe, &gone)) && retry(10, || fs::rename(&old, exe)) {
            start(exe, &[]);
            sys::tell(&texts::get("mover.failed_update", &[("version", VERSION), ("why", &text)]), true);
            return 1;
        }
    }
    if sys::ask(&texts::get("mover.failed_new", &[("version", VERSION), ("why", &text)])) {
        sys::open_page(&format!("{SITE}/releases/latest"));
    }
    1
}

fn free_name(top: &Path) -> Option<PathBuf> {
    (1..100).map(|n| top.join(if n == 1 { "Spiderweb".to_string() } else { format!("Spiderweb ({n})") }))
        .find(|p| !p.exists() || (p.is_dir() && fs::read_dir(p).map_or(false, |mut d| d.next().is_none())))
}

/// A copy of this file in the new folder, started; it deletes this one once it has ended.
fn move_to(new_top: &Path, exe: &Path) -> i32 {
    let copy = new_top.join(folder::EXE);
    let ok = fs::create_dir_all(new_top).is_ok() && fs::copy(exe, &copy).is_ok()
        && start(&copy, &[REMOVE, &exe.display().to_string()]);
    if ok { 0 } else { give_up(exe, false, Fail::Disk) }
}

fn run(exe: &Path, top: &Path, args: &[String]) -> i32 {
    if let Some(i) = args.iter().position(|a| a == REMOVE) {
        if let Some(original) = args.get(i + 1).map(PathBuf::from) {  // (the downloaded copy this one came from)
            std::thread::spawn(move || retry(30, || folder::remove(&original)));
        }
    }
    let old = plus(exe, ".old");  // (an old version's updater left the old exe here)
    let updating = args.iter().any(|a| a == UPDATED) || old.is_file();
    let existing = updating || top.join("user").is_dir();
    let data = top.join("data");
    if !existing && !folder::is_ours(&data) {  // (someone else's "data": never unpacked into)
        let Some(new_top) = free_name(top) else { return give_up(exe, false, Fail::Disk) };
        let name = new_top.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        sys::tell(&texts::get("mover.foreign_data", &[("name", &name)]), false);
        return move_to(&new_top, exe);
    }
    let me = exe.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    let busy = fs::read_dir(top).map(|d| d.flatten().any(|e| {
        let n = e.file_name().to_string_lossy().to_lowercase();
        !OURS.contains(&n.as_str()) && n != me && !n.starts_with("spiderweb.exe.")
    })).unwrap_or(false);
    if !existing && busy {
        if let Some(new_top) = free_name(top) {
            if sys::ask(&texts::get("mover.busy", &[("folder", &top.display().to_string())])) {
                return move_to(&new_top, exe);
            }
        }
    }
    if existing && !folder::is_ours(&data) {
        return give_up(exe, updating, Fail::Disk);
    }
    let shared = Arc::new(ui::Shared {
        cancel: AtomicBool::new(false), finished: AtomicBool::new(false), done: AtomicU32::new(0),
        all: AtomicU32::new(0), label: Mutex::new(texts::get("updates.connecting", &[])),
    });
    let (top2, exe2, s2) = (top.to_path_buf(), exe.to_path_buf(), shared.clone());
    let got = ui::run(&texts::get("mover.title", &[("version", VERSION)]), shared, move || set_up(&top2, &exe2, &s2));
    let new = match got {
        Ok(n) => n,
        Err(f) => return give_up(exe, updating, f),
    };
    // the switch: the old version's exe set aside (deleted only once the starter is in place; "<exe>.new*" names are
    // cleared by the starter at its start too), this file -> "<exe>.old" (the starter deletes it), the starter in
    // its place, started. Any step failing = everything put back, then give_up (the old version again).
    let aside = plus(exe, ".new-previous");
    let had_old = old.is_file();
    if had_old && !retry(30, || fs::rename(&old, &aside)) {
        let _ = folder::remove(&new);
        return give_up(exe, updating, Fail::Disk);
    }
    let put_back = || {
        if had_old {
            retry(10, || fs::rename(&aside, &old));
        }
    };
    if !retry(10, || fs::rename(exe, &old)) {
        let _ = folder::remove(&new);
        put_back();
        return give_up(exe, updating, Fail::Disk);
    }
    if !retry(10, || fs::rename(&new, exe)) {
        retry(10, || fs::rename(&old, exe));
        put_back();
        return give_up(exe, updating, Fail::Disk);
    }
    if !start(exe, if updating { &[UPDATED] } else { &[] }) {
        sys::tell(&texts::get("updates.fail.start_failed_kept", &[]), true);
        return 1;
    }
    if had_old {
        retry(30, || folder::remove(&aside));
    }
    0
}

fn main() {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from(folder::EXE));
    let top = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    texts::init(&top);
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::panic::set_hook(Box::new(|_| {}));  // (no text on a console nobody sees: logged below)
    let code = match std::panic::catch_unwind(|| run(&exe, &top, &args)) {
        Ok(c) => c,
        Err(p) => {
            let what = p.downcast_ref::<String>().cloned().or_else(|| p.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_default();
            log_error(&top, &format!("panic: {what}"));
            let updating = args.iter().any(|a| a == UPDATED) || plus(&exe, ".old").is_file();
            give_up(&exe, updating && !plus(&exe, ".new").is_file(), Fail::Other)
        }
    };
    std::process::exit(code);
}
