"""The app's own settings (the look now; language and others later): user/settings.ini, plain [section] key = value
lines that can be changed by hand. Not here: the project (autosave) and libraries (presets, patterns, shapes).
Read forgivingly (a line typed twice: the last one counts; a line that isn't a setting is skipped, never a reason to
drop the others) and saved by changing only that one line, so whatever else is in the file stays as written."""

import os

from files.about import USER
from files.safefile import write_text

SETTINGS_FILE = os.path.join(USER, "settings.ini")


def _lines():
    try:
        with open(SETTINGS_FILE, encoding="utf-8-sig") as f:
            return f.read().splitlines()
    except (OSError, ValueError):  # (missing / unreadable: like an empty file)
        return []


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
    lines = _lines()
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
    write_text(SETTINGS_FILE, "\n".join(lines) + "\n")
