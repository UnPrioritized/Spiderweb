//! Spiderweb.exe of the folder version: starts the program kept in data\<version>\ next to it. Nothing is unpacked
//! or written. data\version.txt names the version to start (an update writes it last, as its one switch-over step);
//! without it, the version this starter was built with.
//! At every start: the program files' names + sizes against data\<version>\files.txt (only folder listings asked
//! of Windows, nothing read: milliseconds). Missing / wrong = offer to repair (the program's repair mode); Python
//! itself missing = offer the download page. Then data\<version>\python3NN.dll runs boot.pyc with the arguments
//! given (python314._pth there sets Python's paths and keeps any other Python's settings out).
#![windows_subsystem = "windows"]

mod texts;
mod win;

use std::ffi::{c_int, OsStr, OsString};
use std::path::{Path, PathBuf};

const BUILT_VERSION: &str = match option_env!("SPIDERWEB_VERSION") {
    Some(v) => v,
    None => "dev",
};
const DOWNLOADS: &str = "https://github.com/UnPrioritized/Spiderweb/releases/latest";
/// Given to boot.pyc when the user picked Repair (the program's repair mode).
const REPAIR_ARG: &str = "--spiderweb-repair";

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

/// How many files in files.txt are missing or have another size (None = the list itself is missing). Each folder
/// is listed once (Windows hands back every file's size with the listing: ~60 folders, not ~1,250 files asked
/// one by one; a cold hard drive took 0.3 s that way).
fn damaged(data: &Path) -> Option<usize> {
    use std::collections::HashMap;
    let list = std::fs::read_to_string(data.join("files.txt")).ok()?;
    let mut wanted: HashMap<String, Vec<(String, u64)>> = HashMap::new();
    for line in list.lines() {
        // "<size>\t<sha256>\t<place>" (the fingerprint is for the full check by hand; "<size>\t<place>" reads too)
        let mut parts = line.splitn(3, '\t');
        let (Some(size), Some(second)) = (parts.next(), parts.next()) else { continue };
        let name = parts.next().unwrap_or(second);
        let Ok(size) = size.trim().parse::<u64>() else { continue };
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
fn run(dll: &Path, exe: &Path, data: &Path, repair: bool) -> Option<c_int> {
    let py_main = win::py_main(dll)?;
    // python -B (never write .pyc files) boot.pyc [--spiderweb-repair] <the arguments Spiderweb.exe was given>
    let mut args: Vec<OsString> = vec![exe.as_os_str().into(), "-B".into(), data.join("boot.pyc").into_os_string()];
    if repair {
        args.push(REPAIR_ARG.into());
    }
    args.extend(std::env::args_os().skip(1));
    let mut wides: Vec<Vec<u16>> = args.iter().map(|a| win::wide(a)).collect();
    let mut ptrs: Vec<*mut u16> = wides.iter_mut().map(|w| w.as_mut_ptr()).collect();
    ptrs.push(std::ptr::null_mut());
    Some(py_main((ptrs.len() - 1) as c_int, ptrs.as_mut_ptr()))
}

fn main() {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("Spiderweb.exe"));
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
    let mut repair = false;
    let bad = damaged(&data);
    if bad != Some(0) {
        let text = match bad {
            Some(1) => texts.get("starter.repair", Some(1),
                "1 of Spiderweb's program files is missing or damaged.\n\nRepair it now? Spiderweb downloads this \
                 version again and puts back only that file. Your own files stay as they are.\n\nNo = start anyway.",
                &[]),
            Some(n) => texts.get("starter.repair", Some(n),
                "{n} of Spiderweb's program files are missing or damaged.\n\nRepair them now? Spiderweb downloads \
                 this version again and puts back only those files. Your own files stay as they are.\n\nNo = start \
                 anyway.", &[]),
            None => texts.get("starter.repair_list", None,
                "Spiderweb can't check its program files: their list is missing.\n\nRepair them now? Spiderweb \
                 downloads this version again and puts back what is missing or damaged. Your own files stay as \
                 they are.\n\nNo = start anyway.", &[]),
        };
        repair = win::ask(&text);
    }
    match run(&dll, &exe, &data, repair) {
        Some(code) => std::process::exit(code),
        None => broken(),
    }
}
