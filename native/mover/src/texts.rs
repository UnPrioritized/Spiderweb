//! The mover's texts in the language picked in Settings (user\settings.ini [language] code), English when that
//! language lacks one. Built in (build_release.py collects every language's mover.* / updates.* texts): the mover
//! runs before any language file of the new version is there.

use std::path::Path;
use std::sync::OnceLock;

const TEXTS: &str = include_str!(env!("SPIDERWEB_TEXTS"));  // ("<code>\t<key>\t<text>" lines, \n \t \\ escaped)
static CODE: OnceLock<String> = OnceLock::new();

/// A text file as the program reads it: UTF-8 (with or without its BOM) or UTF-16 (saved so by hand in Notepad).
fn read_text(path: &Path) -> String {
    let Ok(b) = std::fs::read(path) else { return String::new() };
    if b.starts_with(&[0xFF, 0xFE]) || b.starts_with(&[0xFE, 0xFF]) {
        let le = b[0] == 0xFF;
        let units: Vec<u16> = b[2..].chunks_exact(2)
            .map(|c| if le { u16::from_le_bytes([c[0], c[1]]) } else { u16::from_be_bytes([c[0], c[1]]) }).collect();
        return String::from_utf16_lossy(&units);
    }
    let b = b.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(&b);
    String::from_utf8_lossy(b).into_owned()
}

/// The language picked in Settings (the last "code" line of [language] wins, as in the program).
pub fn init(top: &Path) {
    let text = read_text(&top.join("user").join("settings.ini"));
    let mut inside = false;
    let mut code = "en".to_string();
    for line in text.lines() {
        let l = line.trim();
        if l.starts_with('[') {
            inside = l.eq_ignore_ascii_case("[language]");
        } else if inside {
            if let Some((k, v)) = l.split_once('=').or_else(|| l.split_once(':')) {
                let v = v.trim();
                if k.trim().eq_ignore_ascii_case("code") && !v.is_empty() && v.len() <= 20
                    && v.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
                    code = v.to_string();
                }
            }
        }
    }
    let _ = CODE.set(code);
}

fn unescape(s: &str) -> String {
    let mut out = String::new();
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '\\' {
            match it.next() {
                Some('n') => out.push('\n'),
                Some('t') => out.push('\t'),
                Some(o) => out.push(o),
                None => {}
            }
        } else {
            out.push(c);
        }
    }
    out
}

fn find(code: &str, key: &str) -> Option<String> {
    TEXTS.lines().find_map(|l| {
        let mut p = l.splitn(3, '\t');
        (p.next()? == code && p.next()? == key).then(|| unescape(p.next().unwrap_or("")))
    })
}

/// The text for key with {places} filled in.
pub fn get(key: &str, places: &[(&str, &str)]) -> String {
    let code = CODE.get().map(String::as_str).unwrap_or("en");
    let mut text = find(code, key).or_else(|| find("en", key)).unwrap_or_else(|| key.to_string());
    for (name, value) in places {
        text = text.replace(&format!("{{{name}}}"), value);
    }
    text
}
