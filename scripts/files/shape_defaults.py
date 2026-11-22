"""The user's own defaults for tool windows (user 2026-11-22, PLAN.md stage D: the "use as default" pin, first in
Gate extend and Gate subtract): what a shape gets the first time the window gives it the tool, instead of the
program's own. user/shape-defaults.ini, beside settings.ini and read / written the same forgiving way (files/
settings.py: hand edits and comments stay): one [section] per window, one key per part, each a JSON value as the
shape stores it (lengths in beats: they follow the PPQ).

    [extend]
    end = {"by": 50.0, "unit": "pct", "plus": 0.0104}
"""

import json
import os

from files.about import USER
from files.safefile import write_text
from files.settings import _entries, _lines, _put_line

DEFAULTS_FILE = os.path.join(USER, "shape-defaults.ini")


def get(section):
    """{key: value} saved for one window ({} = none: the program's own)."""
    out = {}
    for _, sec, key, value in _entries(_lines(path=DEFAULTS_FILE)):
        if sec != section.casefold():
            continue
        try:
            out[key] = json.loads(value)
        except ValueError:  # (a line spoiled by hand: left out, kept in the file)
            continue
    return out


def has(section):
    return bool(get(section))


def put(section, values):
    """Saves one window's defaults ({key: value}); its other keys there are taken out (OSError: can't be
    written)."""
    lines = _lines(writing=True, path=DEFAULTS_FILE)
    lines = _without(lines, section, keep=set(k.casefold() for k in values))
    for key, value in values.items():
        _put_line(lines, section, key, json.dumps(value, separators=(", ", ": ")))
    write_text(DEFAULTS_FILE, "\n".join(lines) + "\n")


def forget(section):
    """The window's defaults taken out: it starts with the program's own again (OSError: can't be written)."""
    lines = _lines(writing=True, path=DEFAULTS_FILE)
    new = _without(lines, section)
    if new != lines:
        write_text(DEFAULTS_FILE, "\n".join(new) + "\n")


def sections():
    """Every window with defaults saved, in the file's order."""
    seen = []
    for _, sec, _, _ in _entries(_lines(path=DEFAULTS_FILE)):
        if sec not in seen and get(sec):
            seen.append(sec)
    return seen


def _without(lines, section, keep=()):
    """lines without the section's keys (but keep), and its heading when nothing is left under it."""
    drop = {i for i, sec, key, _ in _entries(lines) if sec == section.casefold() and key not in keep}
    lines = [line for i, line in enumerate(lines) if i not in drop]
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]") and s[1:-1].strip().casefold() == section.casefold():
            rest = next((j for j in range(i + 1, len(lines)) if lines[j].strip().startswith("[")), len(lines))
            if not any(lines[j].strip() and lines[j].strip()[0] not in "#;" for j in range(i + 1, rest)):
                continue  # (an empty heading)
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out
