"""The app's own settings (look, sound, updates, tips; language later): user/settings.ini, plain [section] key = value
lines that can be changed by hand. Not here: the project (autosave) and libraries (presets, patterns, shapes).
Read forgivingly (a line typed twice: the last one counts; a line that isn't a setting is skipped, never a reason to
drop the others) and saved by changing only that one line, so whatever else is in the file stays as written."""

import locale
import math
import os

from files.about import USER
from files.safefile import write_text

SETTINGS_FILE = os.path.join(USER, "settings.ini")


def _lines(writing=False):
    """The file's lines ([] when it's missing). Saved by an editor in another text format ("Unicode", the system's
    own code page): read in that one, so a single odd line never makes the others count as missing. A file that's
    there but can't be opened reads as empty, and raises OSError when writing (it would be emptied)."""
    try:
        with open(SETTINGS_FILE, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return []
    except OSError:
        if writing:
            raise
        return []
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", "replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode(locale.getpreferredencoding(False) or "latin-1", "replace")
    return text.splitlines()


def _entries(lines):
    """(line number, section, key, value) of every setting line (sections and keys in any capitals)."""
    section = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().casefold()
        elif section is not None and s and s[0] not in "#;":
            key, eq, value = s.partition("=")
            if eq and key.strip():
                yield i, section, key.strip().casefold(), value.strip()


def get(section, key, default=None):
    """The saved value (a string), default when it isn't there."""
    found = default
    for _, sec, k, value in _entries(_lines()):
        if sec == section.casefold() and k == key.casefold():
            found = value
    return found


def put(section, key, value):
    """Saves one value (OSError when the file can't be written)."""
    lines = _lines(writing=True)
    _put_line(lines, section, key, value)
    write_text(SETTINGS_FILE, "\n".join(lines) + "\n")


def put_many(values, lines=None):
    """Saves {(section, key): value, ...} in one write, only when one of them differs from the file's (OSError when
    the file can't be written). lines = the file's, just read."""
    lines = _lines(writing=True) if lines is None else lines
    now = {(sec, k): v for _, sec, k, v in _entries(lines)}
    changed = False
    for (section, key), value in values.items():
        if now.get((section.casefold(), key.casefold())) != str(value).strip():
            _put_line(lines, section, key, value)
            changed = True
    if changed:
        write_text(SETTINGS_FILE, "\n".join(lines) + "\n")


def _put_line(lines, section, key, value):
    new = f"{key} = {value}"
    at = None
    for i, sec, k, _ in _entries(lines):
        if sec == section.casefold() and k == key.casefold():
            at = i
    if at is not None:  # (the one that counts: the last)
        lines[at] = new
    else:
        heads = [i for i, line in enumerate(lines) if line.strip().startswith("[") and line.strip().endswith("]")
                 and line.strip()[1:-1].strip().casefold() == section.casefold()]
        if heads:  # after the section's last line that isn't blank
            end = next((i for i in range(heads[-1] + 1, len(lines)) if lines[i].strip().startswith("[")),
                       len(lines))
            while end > heads[-1] + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, new)
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{section}]", new]


# The program's own settings that older versions kept in the autosave's window settings (user, 2026-10-11): read
# from here when this file has them (the autosave's value only while it hasn't: an older version's, copied over at
# the next autosave), and left out of the autosave once saved here. Window key -> (section, key, kind).
APP_KEYS = {
    "midi_device": ("sound", "midi_out", str),
    "play_voices": ("sound", "voice_limit", int),
    "play_voice_guard": ("sound", "lower_by_itself", bool),
    "play_limiter": ("sound", "omnimidi_limiter", bool),
    "soundfont": ("sound", "soundfont", str),  # (the Hz bass preview's "font": shared with Built-in BASSMIDI)
    "update_check": ("updates", "how_often", str),
    "update_last": ("updates", "last_check", float),
    "seen_version": ("updates", "seen_version", str),
    "tips_on": ("tips", "show_tips", bool),
    "tips_seen": ("tips", "seen", list),
}
NOT_HERE = "not_in_settings_ini"  # (window key: the app settings this file couldn't take, kept in the autosave)


def _read_value(text, kind):
    """A saved text as kind, or None when it doesn't read as one (then the autosave's / the default is used).
    Anything but a path / name may have a " # note" after it."""
    if kind is not str:
        for mark in (" #", " ;", "\t#", "\t;"):
            text = text.split(mark)[0]
        text = text.strip()
    if kind is bool:
        t = text.casefold()
        return True if t in ("yes", "true", "on", "1") else False if t in ("no", "false", "off", "0") else None
    if kind is list:
        return [s.strip() for s in text.split(",") if s.strip()]
    if kind is str:
        return text
    try:
        number = float(text)
        if not math.isfinite(number) or (kind is int and not number.is_integer()):
            return None
        return kind(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _write_value(value, kind):
    """value as the file's text, or None when it can't be saved there safely (then it stays in the autosave)."""
    if kind is bool:
        return "yes" if value is True else "no" if value is False else None
    if kind is list:
        if not isinstance(value, list) or any("," in str(s) for s in value):
            return None
        text = ", ".join(str(s) for s in value)
    elif kind is str:
        text = value if isinstance(value, str) else None
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        text = repr(kind(value))
    else:
        text = None
    if text is None or text != text.strip() or "".join(text.splitlines()) != text:  # (any line end the reading sees)
        return None
    return text


_known = {}  # (section, key) -> the text last read from / saved in the file: only values changed since (or gone from
#             the file) are saved, so lines edited by hand while Spiderweb runs stay unless changed in the program too


def with_app_settings(win):
    """The autosave's window settings with this file's app settings put in. The autosave's value only wins while
    this file has none, or when it's one this file couldn't take last time (listed in NOT_HERE)."""
    win = dict(win) if isinstance(win, dict) else {}
    hz = dict(win.get("hz_preview")) if isinstance(win.get("hz_preview"), dict) else {}
    kept = win.pop(NOT_HERE, None)
    kept = set(kept) if isinstance(kept, list) else set()
    saved = {}
    for _, sec, k, v in _entries(_lines()):
        saved[(sec, k)] = v
    _known.clear()
    _known.update(saved)
    for wkey, (sec, k, kind) in APP_KEYS.items():
        if (sec, k) not in saved or wkey in kept:
            continue
        value = _read_value(saved[(sec, k)], kind)
        if value is not None and wkey == "soundfont":
            hz["font"] = value
            win["hz_preview"] = hz
        elif value is not None:
            win[wkey] = value
    return win


def save_app_settings(win):
    """Saves the app settings found in the window settings win here and takes them out of win; ones that can't be
    saved here (or all, when the file can't be written) stay in win, named in win[NOT_HERE]."""
    hz = win.get("hz_preview")
    found = dict(win, soundfont=hz["font"]) if isinstance(hz, dict) and "font" in hz else win
    values, kept = {}, []
    for wkey, (sec, k, kind) in APP_KEYS.items():
        if wkey in found:
            text = _write_value(found[wkey], kind)
            if text is None:
                kept.append(wkey)
            else:
                values[wkey] = ((sec, k), text)
    try:
        lines = _lines(writing=True)
        now = {(sec, k) for _, sec, k, _ in _entries(lines)}
        changed = {key: text for key, text in values.values() if _known.get(key) != text or key not in now}
        if changed:
            put_many(changed, lines)
    except OSError:
        kept += list(values)
        values = {}
    else:
        _known.update(changed)
    for wkey in values:
        if wkey == "soundfont":
            win["hz_preview"] = {k: v for k, v in hz.items() if k != "font"}
        else:
            win.pop(wkey, None)
    if kept:
        win[NOT_HERE] = kept
