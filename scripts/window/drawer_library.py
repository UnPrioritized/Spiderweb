"""The drawer's shape library (moved out of drawer.py): built-in shapes plus the user's own in spiderweb/shapes/*.json:
names, files, load / save / rename, shape files dropped from elsewhere."""

import json
import os
import re
from tkinter import messagebox

from files.lang import tr
from notes.areas import clean_areas
from notes.custom import clean_strokes, join_strokes
from files.about import USER
from files.safefile import write_text

LIBRARY = os.path.join(USER, "shapes")
# Always in the library (not files). A saved shape with the same name is used instead; deleting it brings these back.
BUILT_IN = {
    "Circle": [{"kind": "ellipse", "box": [0, 0, 1, 1]}],
    "Square": [{"kind": "poly", "pts": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]}],
    "Triangle": [{"kind": "poly", "pts": [[0, 0], [1, 0], [0.5, 1], [0, 0]]}],
}


BAD_CHARS = '<>:"/\\|?*'
NAME_MAX = 100  # (longer names: the file's full path can get too long for Windows)
DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"{d}{n}" for d in ("COM", "LPT") for n in range(1, 10))}


# ---------------------------------------------------------------- library
# A saved shape's name is inside its file ({"name": ..., on the first line); the file is named after it with the
# characters Windows refuses made "_" (user: names may have any character). Files without a name inside (made
# before 1.5) go by their file name.

_names = {}  # file path -> ((modified time, size), the name inside)


def stamp(path):
    """(modified time, size) of a file, or None if it's gone: tells whether it changed since it was read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _name_in(path, stem):
    """The name a saved shape's file holds (its first line; files made by hand: read whole), else its file name."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            line = f.readline().strip()
            if line.startswith('{"name": ') and line.endswith(","):
                got = json.loads(line[:-1] + "}")
            elif line.startswith('{"strokes"'):  # (made before names were kept inside)
                return stem
            else:
                f.seek(0)
                got = json.load(f)
        name = clean_name(got["name"]) if isinstance(got, dict) and isinstance(got.get("name"), str) else ""
    except (OSError, ValueError):
        return stem
    return name or stem


def _saved():
    """The saved shapes: {name in lower case: (name, file path)}."""
    try:
        files = sorted(f for f in os.listdir(LIBRARY) if f.lower().endswith(".json"))
    except OSError:
        return {}
    out = {}
    for f in files:
        path = os.path.join(LIBRARY, f)
        now = stamp(path)
        if now is None:
            continue
        known = _names.get(path)
        if not known or known[0] != now:
            known = _names[path] = (now, _name_in(path, f[:-5]))
        out.setdefault(known[1].lower(), (known[1], path))
    return out


def saved_names():
    return [n for n, _ in _saved().values()]


def shape_path(name):
    """The file of the saved shape called `name` (any upper/lower case), or None."""
    got = _saved().get(name.lower())
    return got[1] if got else None


def shape_stamp(name):
    """What a library shape is right now: its file and (modified time, size), or the built-in it falls back to.
    Equal stamps = the same drawing (panel_custom keeps the shape read until it changes)."""
    path = shape_path(name)
    return (path, stamp(path)) if path else ("built-in", built_in_name(name))


def built_in_name(name):
    """The built-in shape's name if `name` is one (any upper/lower case), else None."""
    return next((b for b in BUILT_IN if b.lower() == name.lower()), None)


def library_names():
    """The built-in shapes first, in their own order (user: always on top; a saved one by that name takes its
    place), then the saved shapes A-Z."""
    names = saved_names()
    by_lower = {n.lower(): n for n in names}
    top = [by_lower.get(b.lower(), b) for b in BUILT_IN]
    return top + sorted((n for n in names if not built_in_name(n)), key=str.lower)


def library_lists():
    """The side panel's Shape box (user 2026-10-14, like the Hz shape box): (the built-in shapes as in
    library_names, the saved ones NEWEST first: last saved / changed on top)."""
    saved = list(_saved().values())
    by_lower = {n.lower(): n for n, _ in saved}
    top = [by_lower.get(b.lower(), b) for b in BUILT_IN]
    rest = [(n, (stamp(p) or (0, 0))[0]) for n, p in saved if not built_in_name(n)]
    return top, [n for n, _ in sorted(rest, key=lambda r: (-r[1], r[0].lower()))]


def placed_strokes(strokes):
    """A drawing's strokes as a placed shape gets them: hidden ones left out (layers list), lines that meet end to
    end joined (custom.join_strokes), no layer data. None if nothing is left."""
    out = [{k: v for k, v in st.items() if k != "layer"} for st in strokes or []
           if not (st.get("layer") or {}).get("hidden")]
    return join_strokes(out) if out else None


def load_drawing(name, layers=False):
    """A library shape's strokes (None if it's missing or broken) and its areas coloured by hand (areas.py). A
    broken file named like a built-in shape gives the built-in one. layers: as drawn, for the drawer (every stroke,
    with its layer data); else as placed (placed_strokes)."""
    strokes, areas = load_layers(name)
    return (strokes if layers else placed_strokes(strokes)), areas


def load_layers(name):
    """load_drawing as drawn."""
    path, b = shape_path(name), built_in_name(name)
    strokes, areas = None, []
    if path:
        try:
            with open(path, encoding="utf-8-sig") as f:
                got = json.load(f)
            strokes, areas = clean_strokes(got.get("strokes")) or None, clean_areas(got.get("areas"))
        except (OSError, ValueError, AttributeError):
            pass
    if strokes is None and b:
        return clean_strokes(BUILT_IN[b]), []
    return strokes, (areas if strokes else [])


def file_stem(name, what="shape"):
    """A shape's (palette's...) name as a file name Windows takes: the characters it refuses -> "_", no dot / space
    at the end, not one of its device names ("CON" -> "CON (shape)")."""
    stem = "".join("_" if c in BAD_CHARS or c < " " else c for c in name).strip().rstrip(". ")
    base, dot, rest = stem.partition(".")
    if base.strip().upper() in DEVICES:
        stem = f"{base} ({what}){dot}{rest}"
    return stem or what


def free_path(name, keep=None):
    """A file for a shape called `name` that no other shape uses ("x (2).json" if "x.json" is taken). keep: the
    shape's own file now (it may stay that one, e.g. only upper / lower case changed)."""
    stem, k = file_stem(name), 2
    path = os.path.join(LIBRARY, stem + ".json")
    while os.path.exists(path) and os.path.normcase(path) != os.path.normcase(keep or ""):
        path, k = os.path.join(LIBRARY, f"{stem} ({k}).json"), k + 1
    return path


def shape_text(name, strokes, areas=()):
    text = '{"name": ' + json.dumps(name) + ',\n "strokes": [\n  ' + ",\n  ".join(json.dumps(st) for st in strokes)
    text += "\n]"
    if areas:
        text += ',\n "areas": ' + json.dumps([[round(u, 6), round(v, 6), c] for u, v, c in areas])
    return text + "}\n"


def save_shape(name, strokes, areas=()):
    """Saves a library shape (over the one with that name, if any). Returns its file."""
    os.makedirs(LIBRARY, exist_ok=True)
    path = shape_path(name) or free_path(name)
    write_text(path, shape_text(name, strokes, areas))
    return path


def rename_shape(old, new):
    """A saved shape gets a new name (its file too, when it can). Raises OSError; ValueError if it can't be read."""
    path = shape_path(old)
    strokes, areas = load_drawing(old, layers=True)
    if not path or strokes is None:
        raise ValueError(old)
    to = free_path(new, keep=path)
    write_text(to, shape_text(new, strokes, areas))
    if os.path.normcase(to) != os.path.normcase(path):
        os.remove(path)


def free_name(name):
    """`name`, or "name (2)", "name (3)"... when the library (built-ins too) already has it."""
    taken = {n.lower() for n in library_names()}
    out, k = name, 2
    while out.lower() in taken:
        out, k = f"{name} ({k})", k + 1
    return out


SHAPE_FILE_MOST = 256 * 2 ** 20  # (bytes: a bigger .json is never read as a shape)


def shape_file(path):
    """A library shape's file from elsewhere (dropped / opened, hunt 2026-10-29 idea): (name, strokes, areas);
    False = a shape file whose drawing can't be read; None = not a shape file (a project, anything else)."""
    from files.project import PROJECT_MARKS
    try:
        if os.path.getsize(path) > SHAPE_FILE_MOST:
            return None
        with open(path, "rb") as f:
            # (shape files start with "name" or "strokes": a big project isn't read twice to find out)
            if not re.match(rb'(\xef\xbb\xbf)?\s*\{\s*"(name|strokes)"', f.read(4096)):
                return None
            f.seek(0)
            raw = f.read()
        got = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(got, dict) or "strokes" not in got or PROJECT_MARKS & got.keys():
        return None
    strokes = clean_strokes(got["strokes"]) if isinstance(got["strokes"], list) else None
    if not strokes:
        return False
    stem = os.path.splitext(os.path.basename(path))[0]
    name = clean_name(got["name"]) if isinstance(got.get("name"), str) else ""
    return name or clean_name(stem) or tr("drawer.imported_shape"), strokes, clean_areas(got.get("areas"))


def offer_shape_file(app, path):
    """Open project / a drop got a library shape's file: offered into the shape library (a taken name gets " (2)")
    instead of "It isn't a Spiderweb project". True = it was one (handled here)."""
    got = shape_file(path)
    if got is None:
        return False
    if got is False:
        messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.shape_file_broken", file=os.path.basename(path)),
                             parent=app)
        return True
    old, strokes, areas = got
    if not messagebox.askyesno(tr("drawer.spiderweb"), tr("drawer.shape_file_offer", name=old), parent=app):
        return True
    name = free_name(old)
    try:
        save_shape(name, strokes, areas)
    except OSError as e:
        messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_save", e=e), parent=app)
        return True
    if app.drawer is not None:
        app.drawer.refresh_list(select=name)
    else:
        app.refresh_custom_names()
    app.status.config(text=tr("drawer.shape_file_added", name=name) if name == old else
                      tr("drawer.shape_file_added_as", name=name, old=old))
    return True


def clean_name(name):
    """A shape's name: no hidden characters (like Tab or line breaks), no spaces around it, at most NAME_MAX
    letters. Any other character is fine (the file name is made safe by file_stem)."""
    return "".join(c for c in name if c >= " ").strip()[:NAME_MAX].strip()
