"""Snap choices: the list in the toolbar (like many MIDI programs: bar, note lengths, dotted and triplet ones) and
custom ones ("Customised snap" window). A snap is stored as a text: "off", "bar", a note length "3/16" (3/16 of a
whole note), or a custom one "c:<count>/<note>/<divided by>" where count is a number, "." (dotted: 1.5 notes) or
".." (double dotted: 1.75 notes), e.g. "c:5/16/1" = five 16ths, "c:./8/3" = a dotted 8th in three."""

from fractions import Fraction

from files.lang import tr

# the list, top to bottom: (snap, the note it shows: (whole-note fraction of the plain note, dots, triplet))
SNAP_LIST = [("off", None), ("bar", None), ("1/1", (1, 0, False)), ("3/4", (2, 1, False)), ("1/2", (2, 0, False)),
             ("1/3", (2, 0, True)), ("3/8", (4, 1, False)), ("1/4", (4, 0, False)), ("1/6", (4, 0, True)),
             ("3/16", (8, 1, False)), ("1/8", (8, 0, False)), ("1/12", (8, 0, True)), ("3/32", (16, 1, False)),
             ("1/16", (16, 0, False)), ("1/24", (16, 0, True)), ("3/64", (32, 1, False)), ("1/32", (32, 0, False)),
             ("1/48", (32, 0, True))]
SNAPS = [s for s, _ in SNAP_LIST]
DEFAULT_SNAP = "1/16"
# the custom window's limits (count, note, divided by) and dots
COUNT_RANGE, NOTE_RANGE, DIV_RANGE = (3, 100), (1, 128), (1, 100)
DOTS = {"/": None, ".": Fraction(3, 2), "..": Fraction(7, 4)}


def custom_parts(snap):
    """"c:3/16/1" -> ("3", 16, 1) (count is a number as text, "." or ".."), or None if it isn't a valid custom one."""
    if not isinstance(snap, str) or not snap.startswith("c:"):
        return None
    try:
        count, note, div = snap[2:].split("/")
        note, div = int(note), int(div)
        if count not in (".", ".."):
            if not COUNT_RANGE[0] <= int(count) <= COUNT_RANGE[1]:
                return None
            count = str(int(count))
    except ValueError:
        return None
    if not (NOTE_RANGE[0] <= note <= NOTE_RANGE[1] and DIV_RANGE[0] <= div <= DIV_RANGE[1]):
        return None
    return count, note, div


def custom_snap(count, note, div):
    return f"c:{count}/{note}/{div}"


def whole_notes(snap):
    """The snap's length in whole notes (Fraction), None for "off" / "bar" / anything unknown."""
    if snap in SNAPS and "/" in snap:
        return Fraction(snap)
    parts = custom_parts(snap)
    if parts is None:
        return None
    count, note, div = parts
    many = DOTS[count] if count in DOTS else int(count)
    return Fraction(many) / note / div


def snap_beats(snap, beats_per_bar):
    """The snap step in beats (quarter notes), None when snapping is off."""
    if snap == "bar":
        return float(beats_per_bar)
    w = whole_notes(snap)
    return float(w * 4) if w else None


def clean_snap(snap):
    """A snap read from a file, made valid. Older versions had 1/64 and 1/128 in the list: they become custom ones
    (three 64ths / 128ths divided by 3 is the same length); "Off" was written with a capital."""
    if snap in SNAPS or custom_parts(snap):
        return snap
    old = {"Off": "off", "1/64": custom_snap(3, 64, 3), "1/128": custom_snap(3, 128, 3)}
    return old.get(snap, DEFAULT_SNAP)


def snap_text(snap):
    """How the snap is shown: "Off", "Bar", "Whole", "3/16", or a custom one ("5/16", "dotted 1/8 ÷ 3")."""
    if snap == "off":
        return tr("snap.off")
    if snap == "bar":
        return tr("snap.bar")
    if snap == "1/1":
        return tr("snap.whole")
    parts = custom_parts(snap)
    if parts is None:
        return snap
    count, note, div = parts
    text = (tr("snap.dotted", note=note) if count == "." else tr("snap.double_dotted", note=note) if count == ".."
            else f"{count}/{note}")
    return text if div == 1 else tr("snap.divided", text=text, div=div)
