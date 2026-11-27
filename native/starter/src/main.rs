//! Spiderweb.exe of the folder version: starts the program kept in data\<version>\ next to it. Nothing is unpacked
//! or written. data\version.txt names the version to start (an update writes it last, as its one switch-over step);
//! without it, the version this starter was built with.
//! At every start: the program files' names + sizes against data\<version>\checksum.txt (only folder listings asked
//! of Windows, nothing read but the list, itself checked against the SHA-256 baked in at build: milliseconds).
//! Missing / wrong = offer to repair (the program's repair mode); Python itself missing = offer the download page. Then data\<version>\python3NN.dll runs boot.pyc with the arguments
//! given (python314._pth there sets Python's paths and keeps any other Python's settings out).
#![windows_subsystem = "windows"]

mod sha256;
mod texts;
mod win;

use std::ffi::{c_int, OsStr, OsString};
use std::path::{Path, PathBuf};

const BUILT_VERSION: &str = match option_env!("SPIDERWEB_VERSION") {
    Some(v) => v,
    None => "dev",
};
/// The program files' list in data\<version>\ ("<size>\t<sha256>\t<place>" per file; build_folder.py writes it).
const LIST: &str = "checksum.txt";
/// SHA-256 of this build's own checksum.txt (build_folder.py makes the list first, then builds this starter with
/// it); None in a plain build. Only checked when starting the version this starter was built with.
const LIST_SHA256: Option<&str> = option_env!("SPIDERWEB_LIST_SHA256");
const DOWNLOADS: &str = "https://github.com/UnPrioritized/Spiderweb/releases/latest";
/// Given to boot.pyc when the user picked Repair (the program's repair mode).
const REPAIR_ARG: &str = "--spiderweb-repair";
/// Given by a repair that worked to the Spiderweb it starts afterwards (a failed / cancelled one starts nothing): no file check, no
/// "closed unexpectedly" question, and no new repair offer if it still can't start (no question loops). Passed on
/// to boot.pyc, which takes it out.
const REPAIRED_ARG: &str = "--spiderweb-repaired";
/// First argument of the copy the starter starts and watches (Spiderweb itself runs there; never passed to Python).
const CHILD_ARG: &str = "--spiderweb-child";
/// The child's exit code when Python's DLL couldn't be loaded (the watching starter then offers the download page).
const NO_PYTHON: c_int = 0x5357_0001;
/// How often the watching starter looks at Spiderweb while it starts.
const WATCH_MS: u64 = 150;

fn usable_version(v: &str) -> bool {
    !v.is_empty() && v.len() <= 40 && v != "." && v != ".."
        && v.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-' || c == '_')
}

fn version(data_root: &Path) -> String {
    match std::fs::read_to_string(data_root.join("version.txt")) {
        Ok(t) if usable_version(t.trim()) => t.trim().to_string(),
        _ => BUILT_VERSION.to_string(),
    }
}

/// data\<version>\python3NN.dll (the Python the folder was built with; not python3.dll).
fn python_dll(data: &Path) -> Option<PathBuf> {
    let entries = std::fs::read_dir(data).ok()?;
    entries.flatten().map(|e| e.path()).find(|p| {
        let name = p.file_name().and_then(OsStr::to_str).unwrap_or("").to_ascii_lowercase();
        name.len() > "python3.dll".len() && name.starts_with("python3") && name.ends_with(".dll")
            && name["python3".len()..name.len() - 4].chars().all(|c| c.is_ascii_digit())
    })
}

/// Python's own files: without them nothing (repair included) can run.
fn core_ok(data: &Path) -> Option<PathBuf> {
    let dll = python_dll(data)?;
    let stem = dll.file_stem()?.to_owned();
    let mut zip = stem.clone();
    zip.push(".zip");
    let mut pth = stem;
    pth.push("._pth");
    for p in [data.join(zip), data.join(pth), data.join("boot.pyc")] {
        if !p.is_file() {
            return None;
        }
    }
    Some(dll)
}

/// How many files in checksum.txt are missing or have another size (None = the list itself is missing or
/// damaged). The list is checked first: for this starter's own version, against the SHA-256 the build baked in
/// (LIST_SHA256); for any version, every line must read and the list must end with a line break (cut short = not).
/// Each folder is listed once (Windows hands back every file's size with the listing: ~60 folders, not ~1,250
/// files asked one by one; a cold hard drive took 0.3 s that way).
fn damaged(data: &Path, own_version: bool) -> Option<usize> {
    use std::collections::HashMap;
    let raw = std::fs::read(data.join(LIST)).ok()?;
    if let (true, Some(want)) = (own_version, LIST_SHA256) {
        if sha256::hex(&raw) != want.to_ascii_lowercase() {
            return None;
        }
    }
    let list = String::from_utf8(raw).ok()?;
    if !list.ends_with('\n') {
        return None;
    }
    let mut wanted: HashMap<String, Vec<(String, u64)>> = HashMap::new();
    for line in list.lines() {
        if line.trim().is_empty() {
            continue;
        }
        // "<size>\t<sha256>\t<place>" (the fingerprint is for the full check by hand; "<size>\t<place>" reads too)
        let mut parts = line.splitn(3, '\t');
        let (Some(size), Some(second)) = (parts.next(), parts.next()) else { return None };
        let name = parts.next().unwrap_or(second);
        let Ok(size) = size.trim().parse::<u64>() else { return None };
        if name.is_empty() {
            return None;
        }
        let name = name.replace('/', "\\");
        let (folder, file) = name.rsplit_once('\\').unwrap_or(("", &name));
        wanted.entry(folder.to_ascii_lowercase()).or_default().push((file.to_lowercase(), size));
    }
    let mut bad = 0;
    for (folder, files) in &wanted {
        let mut have: HashMap<String, u64> = HashMap::new();
        if let Ok(entries) = std::fs::read_dir(data.join(folder)) {
            for e in entries.flatten() {
                if let (Ok(m), Some(n)) = (e.metadata(), e.file_name().to_str()) {
                    if m.is_file() {
                        have.insert(n.to_lowercase(), m.len());
                    }
                }
            }
        }
        bad += files.iter().filter(|(n, s)| have.get(n) != Some(s)).count();
    }
    Some(bad)
}

/// Python's exit code; None when its DLL couldn't be loaded.
/// The last start crashed before its window was up: user\crash-now.log (files/errors.py, rewritten at every start)
/// holds a crash dump, no "==== closed " line and no "speed-ups:" note (written once the window is made). A damaged
/// program file can crash Python itself, so nothing could be said then.
fn crashed_starting(top: &Path) -> bool {
    let Ok(log) = std::fs::read_to_string(top.join("user").join("crash-now.log")) else { return false };
    let dump = log.lines().any(|l| !l.trim().is_empty() && !l.starts_with("==== ") && !l.starts_with("-- "));
    log.contains("==== started") && dump && !log.contains("==== closed ") && !log.contains(" speed-ups: ")
}

/// Python's exit code; None when its DLL couldn't be loaded.
fn run(dll: &Path, exe: &Path, data: &Path, repair: bool) -> Option<c_int> {
    let py_main = win::py_main(dll)?;
    // python -B (never write .pyc files) boot.pyc [--spiderweb-repair] <the arguments Spiderweb.exe was given>
    let mut args: Vec<OsString> = vec![exe.as_os_str().into(), "-B".into(), data.join("boot.pyc").into_os_string()];
    if repair {
        args.push(REPAIR_ARG.into());
    }
    args.extend(std::env::args_os().skip(1).filter(|a| a != REPAIR_ARG && a != CHILD_ARG));
    let mut wides: Vec<Vec<u16>> = args.iter().map(|a| win::wide(a)).collect();
    let mut ptrs: Vec<*mut u16> = wides.iter_mut().map(|w| w.as_mut_ptr()).collect();
    ptrs.push(std::ptr::null_mut());
    Some(py_main((ptrs.len() - 1) as c_int, ptrs.as_mut_ptr()))
}

fn main() {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("Spiderweb.exe"));
    if std::env::args_os().nth(1).is_some_and(|a| a == CHILD_ARG) {
        child_main(&exe);
    }
    let top = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    let data_root = top.join("data");
    let version = version(&data_root);
    let data = data_root.join(&version);
    let texts = texts::Texts::load(&top, &data);
    let folder = data.display().to_string();
    let broken = || {
        let ask = texts.get("starter.broken", None,
            "Spiderweb can't start: its program files are missing or damaged.\n\n{folder}\n\nOpen the download \
             page to get Spiderweb again? Your settings, autosave and exports (the user and output folders) stay \
             as they are.", &[("folder", &folder)]);
        if win::ask(&ask) {
            win::open_page(DOWNLOADS);
        }
        std::process::exit(1);
    };
    let Some(dll) = core_ok(&data) else { broken() };
    // started FOR a repair (Help -> About's "Check program files", a repair offer): straight to it, no question
    let mut repair = std::env::args_os().skip(1).any(|a| a == REPAIR_ARG);
    let repaired = std::env::args_os().skip(1).any(|a| a == REPAIRED_ARG);
    let bad = if repair || repaired { Some(0) } else { damaged(&data, version == BUILT_VERSION) };
    if bad != Some(0) {
        let text = match bad {
            Some(1) => texts.get("starter.repair", Some(1),
                "1 of Spiderweb's program files is missing or damaged.\n\nRepair it now? Spiderweb downloads this \
                 version again and puts back only that file. Your own files stay as they are.\n\nNo = try to start \
                 with the missing or damaged file.", &[]),
            Some(n) => texts.get("starter.repair", Some(n),
                "{n} of Spiderweb's program files are missing or damaged.\n\nRepair them now? Spiderweb downloads \
                 this version again and puts back only those files. Your own files stay as they are.\n\nNo = try to \
                 start with the missing or damaged files.", &[]),
            None => texts.get("starter.repair_list", None,
                "Spiderweb can't check its program files: their list is missing or damaged.\n\nRepair them now? \
                 Spiderweb downloads this version again and puts back what is missing or damaged. Your own files stay as \
                 they are.\n\nNo = try to start without checking them.", &[]),
        };
        repair = win::ask(&text);
    } else if !repair && !repaired && crashed_starting(&top) {
        repair = win::ask(&texts.get("starter.crashed", None,
            "Spiderweb closed unexpectedly while starting last time. Its program files may be damaged.\n\nCheck and \
             repair them now? Spiderweb downloads this version again and puts back only damaged files. Your own \
             files stay as they are.\n\nNo = start as usual.", &[]));
    }
    let log = top.join("user").join("crash-now.log");
    let stamp = || std::fs::metadata(&log).and_then(|m| m.modified()).ok();
    let before = stamp();
    // Spiderweb runs as a second copy of this exe (CHILD_ARG) while this one watches it until its window is up, then
    // leaves: a crash while starting (a damaged file can crash Python itself, taking the whole program with it) is
    // still said at once, not silently (user 2026-11-27).
    let mut cmd = std::process::Command::new(&exe);
    cmd.arg(CHILD_ARG);
    if repair {
        cmd.arg(REPAIR_ARG);
    }
    cmd.args(std::env::args_os().skip(1).filter(|a| a != REPAIR_ARG && a != CHILD_ARG));
    let Ok(mut child) = cmd.spawn() else {
        std::process::exit(match run(&dll, &exe, &data, repair) { Some(c) => c, None => broken() });
    };
    let code = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.code().unwrap_or(1),
            Ok(None) => {}
            Err(_) => std::process::exit(0),
        }
        if stamp() != before && window_up(&log) {
            std::process::exit(0);  // (Spiderweb's window is up: from here its own error handling takes over)
        }
        std::thread::sleep(std::time::Duration::from_millis(WATCH_MS));
    };
    if code == NO_PYTHON {
        broken();
    }
    // Crashed while starting (a dump in a crash-now.log it rewrote, no closing line, window never up): said now.
    if code != 0 && code != 3 && !repair && stamp() != before && crashed_starting(&top) {
        crash_answered(&log);  // (the next start doesn't ask about this crash again)
        if repaired {
            still(&texts, code);
        }
        if win::ask(&texts.get("starter.crashed_now", None,
            "Spiderweb closed unexpectedly while starting. Its program files may be damaged.\n\nRepair them now? \
             Spiderweb downloads this version again and puts back only damaged files. Your own files stay as they \
             are.", &[])) {
            let args: Vec<OsString> = std::env::args_os().skip(1).filter(|a| a != REPAIRED_ARG).collect();
            let _ = std::process::Command::new(&exe).arg(REPAIR_ARG).args(args).current_dir(&top).spawn();
        }
        std::process::exit(code);
    }
    match Some(code) {
        // Ended with an error before Spiderweb even wrote its start line (crash-now.log untouched): damaged
        // Python parts fail there with no word at all (hunt 2026-11-26). Repair mode's own plain message exits 3.
        Some(code) if code != 0 && code != 3 && stamp() == before => {
            if repair {
                broken();
            }
            if repaired {  // (a repair just ran: the download page, never another repair)
                still(&texts, code);
            }
            let ask = texts.get("starter.failed", None,
                "Spiderweb couldn't start: some of its program files seem to be damaged.\n\nRepair them now? \
                 Spiderweb downloads this version again and puts back only the damaged files. Your own files stay \
                 as they are.", &[]);
            if win::ask(&ask) {
                let args: Vec<OsString> = std::env::args_os().skip(1).filter(|a| a != REPAIRED_ARG).collect();
                let _ = std::process::Command::new(&exe).arg(REPAIR_ARG).args(args).current_dir(&top).spawn();
            }
            std::process::exit(code);
        }
        Some(code) => std::process::exit(code),
        None => broken(),
    }
}

/// Right after a repair Spiderweb still can't start: the download page offered, never another repair.
fn still(texts: &texts::Texts, code: c_int) -> ! {
    let ask = texts.get("repair.still", None,
        "Spiderweb still can't start after the repair.\n\nOpen the download page to get Spiderweb again? Your \
         settings, autosave and exports (the user and output folders) stay as they are.", &[]);
    if win::ask(&ask) {
        win::open_page(DOWNLOADS);
    }
    std::process::exit(code);
}

/// Spiderweb's window is up: its crash-now.log (rewritten at this start) has the "speed-ups:" note.
fn window_up(log: &Path) -> bool {
    std::fs::read_to_string(log).is_ok_and(|t| t.contains("==== started") && t.contains(" speed-ups: "))
}

/// The user was told about this start's crash: a closing line, so the next start doesn't ask about it again
/// (files/errors.py still moves the dump into spiderweb-crash.log, as a dump in a run that closed).
fn crash_answered(log: &Path) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(log) {
        let _ = f.write_all(b"==== closed (told at start)\n");
    }
}

/// The copy started by the watching starter (CHILD_ARG): Spiderweb itself, in this process; the checks were done.
fn child_main(exe: &Path) -> ! {
    let top = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    let data = top.join("data").join(version(&top.join("data")));
    let repair = std::env::args_os().skip(1).any(|a| a == REPAIR_ARG);
    let code = core_ok(&data).and_then(|dll| run(&dll, exe, &data, repair));
    std::process::exit(code.unwrap_or(NO_PYTHON));
}
