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


PATHS = "paths"  # a kind: file paths, " | " between them (a "|" can't be in a Windows file name)


# The program's own settings that older versions kept in the autosave's window settings (user, 2026-10-11): read
# from here when this file has them (the autosave's value only while it hasn't: an older version's, copied over at
# the next autosave), and left out of the autosave once saved here. Window key -> (section, key, kind).
APP_KEYS = {
    "midi_device": ("sound", "midi_out", str),
    "play_voices": ("sound", "voice_limit", int),
    "play_voice_guard": ("sound", "lower_by_itself", bool),
    "play_limiter": ("sound", "omnimidi_limiter", bool),
    "loaded_synths": ("sound", "loaded_midi_synths", int),
    "kestrel_voices": ("sound", "kestrel_max_voices", int),
    "kestrel_lower": ("sound", "kestrel_lower_by_itself", bool),
    "kestrel_block": ("sound", "kestrel_block_size", int),
    # (user 2026-11-02: 0 off / 1 brickwall / 2 balanced; a new name so an old "kestrel_limiter = yes" line is just
    # left there, the autosave's old tick read instead: App.take_kestrel_opts)
    "kestrel_limiter": ("sound", "kestrel_limiter_mode", int),
    "kestrel_ceiling_db": ("sound", "kestrel_limiter_ceiling_db", float),
    "kestrel_dc_blocker": ("sound", "kestrel_dc_blocker", bool),
    "kestrel_card": ("sound", "kestrel_graphics_card", str),  # (13g: "backend:name", empty = Kestrel's pick)
    "kestrel_interpolation": ("sound", "kestrel_interpolation", int),
    "kestrel_min_velocity": ("sound", "kestrel_velocity_threshold", int),
    "kestrel_slow_down": ("sound", "kestrel_slow_down", bool),
    "play_slow_down": ("sound", "slow_down", bool),
    "ahead_engine": ("sound", "rendered_ahead_engine", str),  # (files/render_ahead.ENGINES)
    "ahead_seconds": ("sound", "rendered_ahead_seconds", int),
    "ahead_slow_down": ("sound", "rendered_ahead_slow_down", bool),
    "ahead_voices": ("sound", "rendered_ahead_max_voices", int),  # (R1b: render_ahead.DEFAULTS; the card is
    "ahead_block": ("sound", "rendered_ahead_block_size", int),  # Kestrel's, kestrel_graphics_card)
    "ahead_interpolation": ("sound", "rendered_ahead_interpolation", int),
    "ahead_min_velocity": ("sound", "rendered_ahead_velocity_threshold", int),
    "ahead_limiter": ("sound", "rendered_ahead_limiter_mode", int),
    "ahead_ceiling_db": ("sound", "rendered_ahead_limiter_ceiling_db", float),
    "ahead_bass_voices": ("sound", "rendered_ahead_voice_limit", int),
    "ahead_lower": ("sound", "rendered_ahead_lower_by_itself", bool),
    "ahead_exact": ("sound", "rendered_ahead_kestrel_where_merlin_not_exact", bool),
    "ahead_while_stopped": ("sound", "rendered_ahead_make_while_stopped", bool),
    "ahead_dc_blocker": ("sound", "rendered_ahead_dc_blocker", bool),
    "soundfont": ("sound", "soundfont", str),  # (the Hz bass preview's "font": shared with Built-in BASSMIDI)
    "more_soundfonts": ("sound", "more_soundfonts", PATHS),  # (the ones under it in the list, user 2026-10-25)
    "hz_on": ("hz_preview", "on", bool),
    "hz_voices": ("hz_preview", "voice_limit", int),
    "hz_nofx": ("hz_preview", "without_effects", bool),
    "hz_engine": ("hz_preview", "engine", str),  # (the preview's own Engine, user 2026-11-03)
    "hz_volume": ("hz_preview", "volume", float),
    "hz_live_mb": ("hz_preview", "live_keys_memory_mb", int),
    "hz_ring": ("hz_preview", "auto_run_on_seconds", float),  # (Auto's CC run-on measured, App.ring_check)
    "hz_ring_font": ("hz_preview", "auto_run_on_soundfont", str),  # (... for this soundfont: size, time, path)
    "update_check": ("updates", "how_often", str),
    "update_last": ("updates", "last_check", float),
    "seen_version": ("updates", "seen_version", str),
    "tips_on": ("tips", "show_tips", bool),
    "tips_seen": ("tips", "seen", list),
    "domino_256": ("domino", "keys_256", bool),
    "copy_domino": ("domino", "with_ctrl_c", bool),
    "copy_notes": ("copying", "spiderweb_notes", bool),
    "keep_colours": ("channels", "keep_colours", bool),
    "loud_on_top": ("look", "louder_notes_on_top", bool),  # (old tick: read when stacked_notes isn't there yet)
    "stacked_notes": ("look", "stacked_notes", str),  # (roll_draw.STACK_MODES)
    "hidden_shade": ("look", "hidden_note_shade", bool),
    "space_keys": ("view", "space_past_the_keys", int),  # (keyboard heights; SPACE_KEYS)
    "space_bars": ("view", "space_before_the_start_bars", int),  # (SPACE_BARS)
}
SPACE_KEYS = (0, 1, 2, 3, 4)  # Settings → General "Space past the keys": keyboard heights past each end
SPACE_BARS = (0, 1, 2, 4, 8, 16)  # ... "Space before the start": bars before beat 0 (user 2026-11-06)
NOT_HERE = "not_in_settings_ini"  # (window key: the app settings this file couldn't take, kept in the autosave)
# the ones the autosave keeps inside its "hz_preview" settings (1.5.1 sweep: all app-wide): window key -> key there
IN_HZ = {"soundfont": "font", "more_soundfonts": "more_fonts", "hz_on": "on", "hz_voices": "voices", "hz_nofx": "nofx", "hz_volume": "volume",
         "hz_engine": "engine",
         "hz_live_mb": "live_mb", "hz_ring": "ring", "hz_ring_font": "ring_font"}


def _read_value(text, kind):
    """A saved text as kind, or None when it doesn't read as one (then the autosave's / the default is used).
    Anything but a path / name may have a " # note" after it."""
    if kind == PATHS:
        return [s.strip() for s in text.split("|") if s.strip()]
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
    if kind == PATHS:
        if not isinstance(value, list) or any(not isinstance(s, str) or "|" in s or s != s.strip() for s in value):
            return None
        text = " | ".join(value)
    elif kind is list:
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
        if value is not None and wkey in IN_HZ:
            hz[IN_HZ[wkey]] = value
            win["hz_preview"] = hz
        elif value is not None:
            win[wkey] = value
    return win


def save_app_settings(win):
    """Saves the app settings found in the window settings win here and takes them out of win; ones that can't be
    saved here (or all, when the file can't be written) stay in win, named in win[NOT_HERE]."""
    hz = win.get("hz_preview")
    hz = hz if isinstance(hz, dict) else {}
    found = dict(win, **{wkey: hz[k] for wkey, k in IN_HZ.items() if k in hz})
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
    gone = {IN_HZ[wkey] for wkey in values if wkey in IN_HZ}
    if gone:
        win["hz_preview"] = {k: v for k, v in hz.items() if k not in gone}
    for wkey in values:
        if wkey not in IN_HZ:
            win.pop(wkey, None)
    if kept:
        win[NOT_HERE] = kept
