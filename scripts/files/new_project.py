"""Settings → New projects (user 2026-11-18): the song settings New, Load MIDI and a start without an autosave begin
with, so a project file from someone else never becomes the user's next project's setup. Saved in settings.ini
[new_project] (one line each, like every app setting); taken from the open project by its button. The project's own
keys (project_data's) -> the file's names."""

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


def read():
    """{project key: value} of the saved ones that read well; None when nothing is saved yet."""
    out = {}
    for key, (name, kind) in FIELDS.items():
        text = settings.get(SECTION, name)
        value = None if text is None else settings._read_value(text, kind)
        if value is not None:
            out[key] = str(value) if key == "ppq" else value
    return out or None


def save(data):
    """The song settings in data (project_data's) saved (OSError when settings.ini can't be written)."""
    values = {}
    for key, (name, kind) in FIELDS.items():
        if key not in data:
            continue
        value = data[key]
        if key == "ppq":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
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
