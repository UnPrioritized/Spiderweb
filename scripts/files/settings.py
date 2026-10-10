"""The app's own settings (the look now; language and others later): user/settings.ini, plain [section] key = value
lines that can be changed by hand. Not here: the project (autosave) and libraries (presets, patterns, shapes)."""

import configparser
import os

from files.about import USER
from files.safefile import write_text

SETTINGS_FILE = os.path.join(USER, "settings.ini")


def _read():
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(SETTINGS_FILE, encoding="utf-8-sig")
    except (OSError, configparser.Error):  # (broken file: like a missing one)
        return configparser.ConfigParser(interpolation=None)
    return parser


def get(section, key, default=None):
    """The saved value (a string), default when it isn't there."""
    return _read().get(section, key, fallback=default)


def put(section, key, value):
    """Saves one value (OSError when the file can't be written)."""
    parser = _read()
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, str(value))
    lines = []
    for name in parser.sections():
        lines.append(f"[{name}]")
        lines += [f"{k} = {v}" for k, v in parser.items(name)]
        lines.append("")
    write_text(SETTINGS_FILE, "\n".join(lines))
