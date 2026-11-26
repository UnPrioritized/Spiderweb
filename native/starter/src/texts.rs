//! The starter's few messages in the language picked in Settings (user\settings.ini [language] code), read from
//! the same language files the program uses: lang\<code>\ beside Spiderweb.exe first (translations users add),
//! then data\<version>\lang\<code>\, then English there, then the English built in here (the files broken).

use std::path::{Path, PathBuf};

pub struct Texts {
    files: Vec<String>, // (every .json read, the picked language's first)
}

fn usable_code(code: &str) -> bool {
    !code.is_empty() && code.len() <= 20 && code.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
        && !code.starts_with(['_', '-'])
}

fn language(top: &Path) -> String {
    let text = std::fs::read_to_string(top.join("user").join("settings.ini")).unwrap_or_default();
    let mut inside = false;
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('[') {
            inside = l.eq_ignore_ascii_case("[language]");
        } else if inside {
            if let Some((k, v)) = l.split_once('=').or_else(|| l.split_once(':')) {
                if k.trim().eq_ignore_ascii_case("code") && usable_code(v.trim()) {
                    return v.trim().to_string();
                }
            }
        }
    }
    "en".to_string()
}

fn json_files(folder: &Path, one_file: &Path) -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(entries) = std::fs::read_dir(folder) {
        let mut paths: Vec<PathBuf> = entries.flatten().map(|e| e.path())
            .filter(|p| p.extension().map_or(false, |x| x.eq_ignore_ascii_case("json"))).collect();
        paths.sort();
        out.extend(paths.iter().filter_map(|p| std::fs::read_to_string(p).ok()));
    }
    if let Ok(t) = std::fs::read_to_string(one_file) {
        out.push(t); // (an old one-file translation lang\<code>.json)
    }
    out
}

impl Texts {
    pub fn load(top: &Path, data: &Path) -> Texts {
        let code = language(top);
        let mut files = Vec::new();
        for code in [code.as_str(), "en"] {
            for root in [top.join("lang"), data.join("lang")] {
                files.extend(json_files(&root.join(code), &root.join(format!("{code}.json"))));
            }
        }
        Texts { files }
    }

    /// The text for key (with "_one" picked when n is 1), {places} filled in; `english` when no file has it.
    pub fn get(&self, key: &str, n: Option<usize>, english: &str, places: &[(&str, &str)]) -> String {
        let one = format!("{key}_one");
        let keys: Vec<&str> = if n == Some(1) { vec![one.as_str(), key] } else { vec![key] };
        let mut found = None;
        'files: for f in &self.files {
            for k in &keys {
                if let Some(t) = find(f, k) {
                    found = Some(t);
                    break 'files;
                }
            }
        }
        let mut text = found.unwrap_or_else(|| english.to_string());
        if let Some(n) = n {
            text = text.replace("{n}", &n.to_string());
        }
        for (name, value) in places {
            text = text.replace(&format!("{{{name}}}"), value);
        }
        text
    }
}

/// The string value of "key": "..." anywhere in a JSON text (the language files are flat key -> text).
fn find(json: &str, key: &str) -> Option<String> {
    let b = json.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'"' {
            let (s, end) = string_at(json, i)?;
            let mut j = end;
            while j < b.len() && b[j].is_ascii_whitespace() {
                j += 1;
            }
            if j < b.len() && b[j] == b':' {
                j += 1;
                while j < b.len() && b[j].is_ascii_whitespace() {
                    j += 1;
                }
                if j < b.len() && b[j] == b'"' {
                    let (value, after) = string_at(json, j)?;
                    if s == key {
                        return Some(value);
                    }
                    i = after;
                    continue;
                }
            }
            i = end;
        } else {
            i += 1;
        }
    }
    None
}

/// The JSON string starting at the quote at `start`: (its text, the place after its closing quote).
fn string_at(json: &str, start: usize) -> Option<(String, usize)> {
    let mut out = String::new();
    let mut chars = json[start + 1..].char_indices();
    let mut pending: Option<u32> = None; // (a \uD8xx waiting for its second half)
    while let Some((k, c)) = chars.next() {
        match c {
            '"' => return Some((out, start + 1 + k + 1)),
            '\\' => {
                let (_, e) = chars.next()?;
                match e {
                    'n' => out.push('\n'),
                    't' => out.push('\t'),
                    'r' => out.push('\r'),
                    'b' => out.push('\u{8}'),
                    'f' => out.push('\u{c}'),
                    'u' => {
                        let mut v = 0u32;
                        for _ in 0..4 {
                            v = v * 16 + chars.next()?.1.to_digit(16)?;
                        }
                        if (0xD800..0xDC00).contains(&v) {
                            pending = Some(v);
                            continue;
                        }
                        if let (Some(hi), true) = (pending.take(), (0xDC00..0xE000).contains(&v)) {
                            v = 0x10000 + ((hi - 0xD800) << 10) + (v - 0xDC00);
                        }
                        out.push(char::from_u32(v).unwrap_or('\u{FFFD}'));
                    }
                    other => out.push(other),
                }
            }
            c => out.push(c),
        }
    }
    None
}
