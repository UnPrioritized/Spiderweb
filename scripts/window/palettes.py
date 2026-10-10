"""Note colour palettes (user, 2026-10-11): the 15 channel slot colours notes are drawn with (roll_shared.SLOT_COLORS;
the drawer's strokes and Hz bass layers use them too). Saved with the app, not the project, as switchable profiles:
"Default" is built in (today's colours: the light look's, or look.NOTES_DARK in the dark look) and can't be renamed
or deleted; the user's are files in user/palettes/ (one file each, so a palette can be handed to someone), named
inside the file like the shape library. The picked one: settings.ini [look] palette. Switching repaints at once."""

import json
import os

from files import settings
from files.about import USER
from files.safefile import write_text
from roll import roll_shared
from window.drawer import clean_name, file_stem

FOLDER = os.path.join(USER, "palettes")
DEFAULT = ""  # (the built-in palette's id; its shown name is tr("palette.default"))
SLOTS = len(roll_shared.SLOT_COLORS)
BUILT_IN = [tuple(p) for p in roll_shared.SLOT_COLORS]  # (as the look made them at start)


def _colour(c):
    c = str(c).strip().lower()
    return c if len(c) == 7 and c[0] == "#" and all(ch in "0123456789abcdef" for ch in c[1:]) else None


def clean(colours):
    """A palette's colours as SLOTS (fill, edge) pairs; missing / wrong ones = the built-in one in that slot."""
    out = []
    for k in range(SLOTS):
        pair = colours[k] if isinstance(colours, list) and k < len(colours) else None
        fill = edge = None
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            fill, edge = _colour(pair[0]), _colour(pair[1])
        out.append((fill or BUILT_IN[k][0], edge or BUILT_IN[k][1]))
    return out


def read(path):
    """(name, colours) of a palette file, None when it can't be read."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            got = json.load(f)
        name = clean_name(str(got.get("name") or os.path.splitext(os.path.basename(path))[0]))
        return (name, clean(got.get("colours"))) if name else None
    except (OSError, ValueError, AttributeError):
        return None


def user_palettes():
    """{name: (path, colours)} of the user's palettes, by name (a broken file: left out)."""
    out = {}
    try:
        files = sorted(f for f in os.listdir(FOLDER) if f.lower().endswith(".json"))
    except OSError:
        return out
    for f in files:
        got = read(os.path.join(FOLDER, f))
        if got and got[0].casefold() not in {n.casefold() for n in out}:
            out[got[0]] = (os.path.join(FOLDER, f), got[1])
    return out


def names():
    """The palettes in the list's order: DEFAULT first, then the user's (by name)."""
    return [DEFAULT] + sorted(user_palettes(), key=str.casefold)


def colours_of(name):
    if name != DEFAULT:
        got = user_palettes().get(name)
        if got:
            return got[1]
    return list(BUILT_IN)


def picked():
    """The palette in use (DEFAULT when the saved one is gone)."""
    name = settings.get("look", "palette", DEFAULT) or DEFAULT
    return name if name == DEFAULT or name in user_palettes() else DEFAULT


def taken(name, but=None):
    """Another palette already has this name (capitals or not; the built-in one's too)."""
    from files.lang import tr
    low = name.casefold()
    return low == tr("palette.default").casefold() or any(
        n.casefold() == low for n in user_palettes() if n != but)


def _path_for(name, keep=None):
    stem, k = file_stem(name), 2
    path = os.path.join(FOLDER, stem + ".json")
    while os.path.exists(path) and os.path.normcase(path) != os.path.normcase(keep or ""):
        path, k = os.path.join(FOLDER, f"{stem} ({k}).json"), k + 1
    return path


def save(name, colours, keep=None):
    """Writes the palette (OSError when it can't be). keep: its file now (renaming)."""
    os.makedirs(FOLDER, exist_ok=True)
    path = _path_for(name, keep)
    text = '{"name": ' + json.dumps(name) + ',\n "colours": [\n  ' + ",\n  ".join(
        json.dumps(list(p)) for p in colours) + "\n ]\n}\n"
    write_text(path, text)
    if keep and os.path.normcase(keep) != os.path.normcase(path):
        os.remove(keep)
    return path


def rename(old, new):
    path, colours = user_palettes()[old]
    in_use = picked() == old
    save(new, colours, keep=path)
    if in_use:
        settings.put("look", "palette", new)


def delete(name):
    os.remove(user_palettes()[name][0])
    if settings.get("look", "palette", DEFAULT) == name:
        settings.put("look", "palette", DEFAULT)


def apply(colours, app=None):
    """Notes are drawn with these colours from now on (every table made from them made again); app: repainted."""
    from roll import roll_draw
    from window import velocity
    roll_shared.SLOT_COLORS[:] = clean(colours)
    roll_draw.NOTE_COLORS[:] = [(roll_shared.fade(f, 1 - level * 4 / 124), b) for f, b in roll_shared.SLOT_COLORS
                                + [roll_shared.SELECTED_COLOR, roll_shared.DRAFT_COLOR] for level in range(32)]
    roll_draw.NOTE_RGB = roll_draw.np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair]
                                             for pair in roll_draw.NOTE_COLORS], roll_draw.np.uint8)
    roll_draw._tables.clear()
    velocity.LAYERS[:] = ([(roll_shared.fade(f), roll_shared.fade(b, 0.55)) for f, b in roll_shared.SLOT_COLORS]
                          + list(roll_shared.SLOT_COLORS) + [roll_shared.SELECTED_COLOR, roll_shared.DRAFT_COLOR])
    if app is None:
        return
    roll = app.roll
    roll._note_pic = roll._carry = roll._draft_base = None
    roll.request_redraw()
    vel = getattr(app, "vel", None)
    if vel is not None:
        vel._pic = None
        vel.request_redraw()
    for name in ("hz_window", "drawer"):  # (open windows drawing with them)
        w = getattr(app, name, None)
        try:
            if w is not None and w.winfo_exists():
                w.redraw()
                if name == "hz_window":  # (its layers strip)
                    w.layers.redraw()
        except Exception:  # (one that can't be redrawn now does at its next change)
            pass


def use(name, app=None):
    """Picks a palette: saved as the one in use (OSError when it can't be) and put on."""
    settings.put("look", "palette", name)
    apply(colours_of(name), app)


def start():
    """At start, before anything is drawn: the saved palette put on."""
    name = picked()
    if name != DEFAULT:
        apply(colours_of(name))
