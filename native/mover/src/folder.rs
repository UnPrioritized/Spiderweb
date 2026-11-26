//! The folder version's zip unpacked safely, and version folders' lists: the same rules as scripts/files/folder_zip.py
//! (keep both in step). NEVER TOUCHES WHAT ISN'T SPIDERWEB'S: unpacking goes into data\<version>.part-<random>\,
//! renamed data\<version>\ only once whole (sizes checked against its files.txt); a version folder is deleted only by
//! its own files.txt.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use crate::Fail;

const TOP: &str = "Spiderweb/";
pub const EXE: &str = "Spiderweb.exe";
const TOP_FILES: [&str; 4] = ["LICENSE", "README.txt", "THIRD-PARTY-NOTICES.txt", "SPIDERWEB-NOTES-FORMAT.md"];
const UNPACKED_MOST: u64 = 2 << 30;

/// root\rel when it stays inside root (no "..", "." or empty parts, no drive or absolute part), else None.
pub fn inside(root: &Path, rel: &str) -> Option<PathBuf> {
    let rel = rel.replace('\\', "/");
    if rel.is_empty() || rel.starts_with('/') || rel.contains(':')
        || rel.split('/').any(|p| p.is_empty() || p == "." || p == "..") {
        return None;
    }
    let mut out = root.to_path_buf();
    for p in rel.split('/') {
        out.push(p);
    }
    Some(out)
}

/// {place: size} from a version folder's files.txt; None when it has none (= not ours).
pub fn listed(folder: &Path) -> Option<HashMap<String, u64>> {
    let text = fs::read_to_string(folder.join("files.txt")).ok()?;
    let mut out = HashMap::new();
    for line in text.lines() {
        let parts: Vec<&str> = line.split('\t').collect();
        let (size, name) = match parts.len() {
            3 => (parts[0], parts[2]),
            2 => (parts[0], parts[1]),
            _ => continue,
        };
        if let Ok(s) = size.parse::<u64>() {
            out.insert(name.replace('\\', "/"), s);
        }
    }
    Some(out)
}

pub fn remove(path: &Path) -> std::io::Result<()> {
    match fs::remove_file(path) {
        Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
            if let Ok(m) = fs::metadata(path) {
                let mut p = m.permissions();
                #[allow(clippy::permissions_set_readonly_false)]
                p.set_readonly(false);
                let _ = fs::set_permissions(path, p);
            }
            fs::remove_file(path)
        }
        other => other,
    }
}

/// Deletes a version folder the way it came: its listed files (+ files.txt), then folders left empty, deepest first.
pub fn remove_listed(folder: &Path) -> bool {
    let Some(files) = listed(folder) else { return false };
    for rel in files.keys() {
        if let Some(p) = inside(folder, rel) {
            if p.is_file() {
                let _ = remove(&p);
            }
        }
    }
    let _ = remove(&folder.join("files.txt"));
    let mut dirs = Vec::new();
    collect_dirs(folder, &mut dirs);
    dirs.sort_by_key(|d| std::cmp::Reverse(d.as_os_str().len()));
    for d in dirs {
        let _ = fs::remove_dir(d);  // (only empty ones)
    }
    !folder.exists()
}

fn collect_dirs(d: &Path, out: &mut Vec<PathBuf>) {
    out.push(d.to_path_buf());
    if let Ok(entries) = fs::read_dir(d) {
        for e in entries.flatten() {
            if e.file_type().map_or(false, |t| t.is_dir()) {
                collect_dirs(&e.path(), out);
            }
        }
    }
}

/// A data\ folder Spiderweb made: missing, empty, or holding version.txt / version folders with a files.txt.
pub fn is_ours(data: &Path) -> bool {
    let Ok(entries) = fs::read_dir(data) else { return !data.exists() };
    let names: Vec<_> = entries.flatten().collect();
    names.is_empty() || names.iter().any(|e| e.file_name() == "version.txt" || listed(&e.path()).is_some())
}

struct Entry {
    name: String,
    method: u16,
    packed: u64,
    size: u64,
    local: u64,
}

fn u16_at(b: &[u8], i: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(i..i + 2)?.try_into().ok()?))
}
fn u32_at(b: &[u8], i: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(i..i + 4)?.try_into().ok()?))
}
fn u64_at(b: &[u8], i: usize) -> Option<u64> {
    Some(u64::from_le_bytes(b.get(i..i + 8)?.try_into().ok()?))
}

/// The zip's table of contents (zip64 too: Windows' tar writes it).
fn entries(z: &[u8]) -> Option<Vec<Entry>> {
    let from = z.len().saturating_sub(22 + 65535);
    let eocd = (from..=z.len().checked_sub(22)?).rev().find(|&i| u32_at(z, i) == Some(0x0605_4b50))?;
    let (mut count, mut at) = (u16_at(z, eocd + 10)? as u64, u32_at(z, eocd + 16)? as u64);
    if count == 0xFFFF || at == 0xFFFF_FFFF {
        let loc = eocd.checked_sub(20)?;
        if u32_at(z, loc)? != 0x0706_4b50 {
            return None;
        }
        let e64 = u64_at(z, loc + 8)? as usize;
        if u32_at(z, e64)? != 0x0606_4b50 {
            return None;
        }
        count = u64_at(z, e64 + 32)?;
        at = u64_at(z, e64 + 48)?;
    }
    let mut out = Vec::new();
    let mut i = at as usize;
    for _ in 0..count {
        if u32_at(z, i)? != 0x0201_4b50 {
            return None;
        }
        let method = u16_at(z, i + 10)?;
        let (mut packed, mut size) = (u32_at(z, i + 20)? as u64, u32_at(z, i + 24)? as u64);
        let (nlen, elen, clen) = (u16_at(z, i + 28)? as usize, u16_at(z, i + 30)? as usize, u16_at(z, i + 32)? as usize);
        let mut local = u32_at(z, i + 42)? as u64;
        let name = String::from_utf8_lossy(z.get(i + 46..i + 46 + nlen)?).into_owned();
        // zip64 extra: the 8-byte values for those marked 0xFFFFFFFF, in this order
        let extra = z.get(i + 46 + nlen..i + 46 + nlen + elen)?;
        let mut j = 0;
        while j + 4 <= extra.len() {
            let (id, len) = (u16_at(extra, j)?, u16_at(extra, j + 2)? as usize);
            if id == 1 {
                let mut k = j + 4;
                for v in [&mut size, &mut packed, &mut local] {
                    if *v == 0xFFFF_FFFF {
                        *v = u64_at(extra, k)?;
                        k += 8;
                    }
                }
            }
            j += 4 + len;
        }
        out.push(Entry { name, method, packed, size, local });
        i += 46 + nlen + elen + clen;
    }
    Some(out)
}

fn contents(z: &[u8], e: &Entry) -> Result<Vec<u8>, Fail> {
    let l = e.local as usize;
    if u32_at(z, l) != Some(0x0403_4b50) {
        return Err(Fail::BadFile);
    }
    let start = l + 30 + u16_at(z, l + 26).ok_or(Fail::BadFile)? as usize + u16_at(z, l + 28).ok_or(Fail::BadFile)? as usize;
    let packed = z.get(start..start + e.packed as usize).ok_or(Fail::BadFile)?;
    let out = match e.method {
        0 => packed.to_vec(),
        8 => miniz_oxide::inflate::decompress_to_vec_with_limit(packed, e.size as usize).map_err(|_| Fail::BadFile)?,
        _ => return Err(Fail::BadFile),
    };
    if out.len() as u64 != e.size {
        return Err(Fail::BadFile);
    }
    Ok(out)
}

/// The checked zip (bytes) -> data\<version>\ (through its own part folder), the starter as new_exe, README +
/// licences into top. Err = nothing of it left.
pub fn unpack(z: &[u8], version: &str, data: &Path, new_exe: &Path, top: &Path, cancel: &AtomicBool,
              progress: &mut dyn FnMut(u64, u64)) -> Result<(), Fail> {
    let stage = data.join(format!("{version}.part-{}", crate::sys::random_hex()));
    let r = unpack_into(z, version, data, &stage, new_exe, top, cancel, progress);
    if r.is_err() {
        let _ = fs::remove_dir_all(&stage);  // (an unpacking's own folder: only what it wrote)
        let _ = remove(new_exe);
    }
    r
}

#[allow(clippy::too_many_arguments)]
fn unpack_into(z: &[u8], version: &str, data: &Path, stage: &Path, new_exe: &Path, top: &Path, cancel: &AtomicBool,
               progress: &mut dyn FnMut(u64, u64)) -> Result<(), Fail> {
    let inner = format!("{TOP}data/{version}/");
    let all: Vec<Entry> = entries(z).ok_or(Fail::BadFile)?.into_iter().filter(|e| !e.name.ends_with('/')).collect();
    if all.iter().map(|e| e.size).sum::<u64>() > UNPACKED_MOST {
        return Err(Fail::TooBig);
    }
    let exe_name = format!("{TOP}{EXE}");
    if !all.iter().any(|e| e.name == exe_name) || !all.iter().any(|e| e.name == format!("{inner}files.txt")) {
        return Err(Fail::BadFile);
    }
    let mut tops = Vec::new();
    for (n, e) in all.iter().enumerate() {
        if cancel.load(Ordering::Relaxed) {
            return Err(Fail::Cancelled);
        }
        let dest = if e.name == exe_name {
            new_exe.to_path_buf()
        } else if let Some(rel) = e.name.strip_prefix(&inner) {
            inside(stage, rel).ok_or(Fail::BadFile)?
        } else {
            if let Some(rel) = e.name.strip_prefix(TOP) {
                if TOP_FILES.contains(&rel) {
                    tops.push(e);
                }
            }
            continue;
        };
        let bytes = contents(z, e)?;
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent).map_err(|_| Fail::Disk)?;
        }
        fs::write(&dest, bytes).map_err(|_| Fail::Disk)?;
        progress(n as u64 + 1, all.len() as u64);
    }
    for (rel, size) in listed(stage).unwrap_or_default() {
        let ok = inside(stage, &rel).and_then(|p| fs::metadata(p).ok()).map_or(false, |m| m.is_file() && m.len() == size);
        if !ok {
            return Err(Fail::BadFile);
        }
    }
    let fin = data.join(version);
    if fin.is_dir() && !remove_listed(&fin) {
        return Err(Fail::Disk);
    }
    fs::rename(stage, &fin).map_err(|_| Fail::Disk)?;
    for e in tops {  // (after the folder is in place: a failing readme never stops it)
        if let (Ok(b), Some(rel)) = (contents(z, e), e.name.strip_prefix(TOP)) {
            let _ = fs::write(top.join(rel), b);
        }
    }
    Ok(())
}
