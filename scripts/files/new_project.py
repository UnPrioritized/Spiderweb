"""Settings → New projects (user 2026-11-18): the song settings New, Load MIDI and a start without an autosave begin
with, so a project file from someone else never becomes the user's next project's setup. Saved in settings.ini
[new_project] (one line each, like every app setting); taken from the open project by its button. The project's own
keys (project_data's) -> the file's names."""

import os

from files import settings

SECTION = "new_project"
# project_data key -> (settings.ini key, kind)
FIELDS = {
    "ppq": ("ppq", int),
    "keys": ("keys", int),
    "channel_mode": ("channels", str),
    "channel_split": ("multi_split", str),
    "use_ch10": ("use_channel_10", bool),
    "ch10_order": ("channel_10_in_order", bool),
    "new_rounds": ("repeated_colours_new_channels", bool),
    "pad_rounds": ("empty_tracks_between", bool),
    "single_split": ("single_split_around", bool),
    "multi_cut": ("multi_no_overlaps", bool),
    "multi_later": ("multi_later_tracks_win", bool),
    "multi_stretch": ("multi_cut_and_stretch", bool),
    "edo31": ("edo31_view", bool),
    "edo31_audio": ("edo31_audio", bool),
    "edo31_lanes": ("edo31_pitch_kept", bool),
    "domino_start": ("domino_start", str),
    "output": ("output_file", str),
}
FIT_VIEW = "fit_view_on_new"  # (the tick: New = Fit view instead of the zoom kept, user 2026-11-18)


def _usable(key, value):
    """A saved value as New would use it, or None when it can't be (hand-edited lines: 70000, 200 keys...); text
    choices in any capitals."""
    from files.domino_clip import DOMINO_STARTS
    from notes.engine import CHANNEL_MODES, SPLITS
    from notes.paths import KEYS
    if key == "ppq":
        return str(value) if 1 <= value <= 65535 else None
    if key == "keys":
        return value if value in KEYS else None
    choices = {"channel_mode": CHANNEL_MODES, "channel_split": SPLITS,
               "domino_start": [v for v, _ in DOMINO_STARTS]}.get(key)
    if choices is not None:
        return next((c for c in choices if c.casefold() == value.strip().casefold()), None)
    return value


def read():
    """{project key: value} of the saved ones that New can use; None when nothing is saved yet."""
    out = {}
    for key, (name, kind) in FIELDS.items():
        text = settings.get(SECTION, name)
        value = None if text is None else settings._read_value(text, kind)
        if value is not None:
            value = _usable(key, value)
        if value is not None:
            out[key] = value
    return out or None


def short_output(path):
    """Spiderweb's own output folder saved as the file name alone (output_path counts it from there), so moving the
    Spiderweb folder keeps it working; elsewhere kept whole; empty stays empty (= spiderweb.mid there)."""
    from files.project import OUTPUT_DIR, output_path
    if not path.strip():
        return ""
    full = output_path(path)
    if os.path.normcase(os.path.dirname(full)) == os.path.normcase(os.path.normpath(OUTPUT_DIR)):
        return os.path.basename(full)
    return path.strip()


def save(data):
    """The song settings in data (project_data's) saved (OSError when settings.ini can't be written). Ticks missing
    from data count as off (project_data leaves the overlap ticks out when off)."""
    values = {}
    for key, (name, kind) in FIELDS.items():
        if key not in data and kind is not bool:
            continue
        value = data.get(key, False)
        if key == "ppq":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif key == "output" and isinstance(value, str):
            value = short_output(value)
        text = settings._write_value(value, kind)
        if text is not None:
            values[(SECTION, name)] = text
    settings.put_many(values)


USED = "use_these"  # (the master tick, user 2026-11-18: off = New / Load MIDI keep the open project's, as before)


def used_on():
    return settings._read_value(settings.get(SECTION, USED, "yes"), bool) is not False


def set_used(on):
    settings.put_quiet(SECTION, USED, "yes" if on else "no")


def used():
    """read() when the master tick is on, else None (the open project's settings stay)."""
    return read() if used_on() else None


def fit_view():
    return settings._read_value(settings.get(SECTION, FIT_VIEW, "no"), bool) is True


def set_fit_view(on):
    settings.put_quiet(SECTION, FIT_VIEW, "yes" if on else "no")
