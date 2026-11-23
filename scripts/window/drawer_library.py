"""The drawer's shape library (moved out of drawer.py): built-in shapes plus the user's own in user/shapes/**.json
(sub folders, any depth): names, files, folders, load / save / rename / move, shape files dropped from elsewhere."""

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
DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"{d}{n}" for d in ("COM", "LPT") for n in list(range(1, 10)) + ["¹", "²", "³"])}
# (COM¹ / LPT² ...: Windows refuses those too, hunt 11-24)


# ---------------------------------------------------------------- library
# A saved shape's name is inside its file ({"name": ..., on the first line); the file is named after it with the
# characters Windows refuses made "_" (user: names may have any character). Files without a name inside (made
# before 1.5) go by their file name.

_names = {}  # file path -> ((modified time, size), the name inside)
# SUB FOLDERS (PLAN.md, user 2026-11-24): real folders inside the library, any depth, what File Explorer shows. A
# shape's folder = "" at the top, else its path inside the library with "/" ("Spikes/Old"). Names stay unique over
# the whole library (placed shapes, projects, Nested / Tiles / Stir find shapes by name): when two files hold the
# same name, the FIRST one counts (top files, then folders A-Z, each folder's files before its sub folders); the
# other is still listed, marked (library_entries "same").
FOLDER_DEPTH = 32  # (deeper folders are left out: a loop of links can't hang the scan)


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


def folder_path(folder):
    """A library folder ("" = the library itself, "A/B") as a path on disk."""
    return os.path.join(LIBRARY, *folder.split("/")) if folder else LIBRARY


def folder_of(path):
    """The library folder a file / folder on disk sits in ("" = the top)."""
    rel = os.path.relpath(os.path.dirname(path), LIBRARY)
    return "" if rel in (".", "") else "/".join(rel.split(os.sep))


def _scan():
    """([(name, path, folder, (modified time, size))] of every saved shape in the order above, [every folder]).
    Names come from _names (read again only when a file's stamp changed)."""
    files, folders, seen = [], [], set()
    been = {os.path.normcase(os.path.realpath(LIBRARY))}  # (each real folder read once: two links back into the
    #                                                        library doubled the work every level, hunt 2026-11-24)

    def visit(full, rel, depth):
        try:
            with os.scandir(full) as it:
                found = sorted(it, key=lambda e: (e.name.lower(), e.name))
        except OSError:
            return
        subs = []
        for e in found:
            try:
                if e.is_dir():
                    subs.append(e)
                elif e.name.lower().endswith(".json") and e.is_file():
                    st = e.stat()  # (Windows: from the folder listing itself, no extra disk read)
                    now = (st.st_mtime_ns, st.st_size)
                    known = _names.get(e.path)
                    if not known or known[0] != now:
                        known = _names[e.path] = (now, _name_in(e.path, e.name[:-5]))
                    seen.add(e.path)
                    files.append((known[1], e.path, rel, now))
            except OSError:
                pass
        for e in subs:
            real = os.path.normcase(os.path.realpath(e.path))
            if real in been:  # (a link to a folder read already, e.g. back to the library: left out)
                continue
            been.add(real)
            sub = f"{rel}/{e.name}" if rel else e.name
            folders.append(sub)
            if depth < FOLDER_DEPTH:
                visit(e.path, sub, depth + 1)

    visit(LIBRARY, "", 1)
    if len(_names) > len(seen):  # (files moved / deleted: their names aren't kept forever)
        for gone in [p for p in _names if p not in seen]:
            del _names[gone]
    return files, folders


def _saved():
    """The saved shapes: {name in lower case: (name, file path)} (the first file holding a name)."""
    out = {}
    for name, path, _, _ in _scan()[0]:
        out.setdefault(name.lower(), (name, path))
    return out


def library_entries():
    """Every saved shape's file, for the lists: [{"name", "path", "folder", "stamp", "same"}] in the scan's order;
    "same" = the folder of the file whose name counts when this one holds a name already used (else None)."""
    first, out = {}, []
    for name, path, folder, now in _scan()[0]:
        key = name.lower()
        same = first[key] if key in first else None
        first.setdefault(key, folder)
        out.append({"name": name, "path": path, "folder": folder, "stamp": now, "same": same})
    return out


def library_folders():
    """Every folder in the library ("A", "A/B", ...), each before its sub folders, A-Z."""
    return _scan()[1]


def shape_folder(name):
    """The folder of the saved shape called `name` ("" = the top), or None (not saved)."""
    path = shape_path(name)
    return folder_of(path) if path else None


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


def library_choices():
    """The shape dropdowns with folders (Shape box, Nested, Tiles, Stir, Deform…; user 2026-11-24): (built-ins as
    in library_names, the loose saved shapes newest first, [(heading, depth, names newest first)] per folder in the
    drawer's order: a folder shown when it or a folder inside holds a shape). A second file with a used name is left
    out (its name finds the other)."""
    entries = [e for e in library_entries() if e["same"] is None]
    every = library_folders()  # (read once: each call looks at the disk)
    by_lower = {e["name"].lower(): e["name"] for e in entries}
    top = [by_lower.get(b.lower(), b) for b in BUILT_IN]
    newest = lambda es: [e["name"] for e in sorted(es, key=lambda e: (-e["stamp"][0], e["name"].lower()))]
    shapes_in = {}
    for e in entries:
        if not built_in_name(e["name"]):
            shapes_in.setdefault(e["folder"], []).append(e)
    full = {f for f in shapes_in if f}
    for f in list(full):
        while "/" in f:
            f = f.rpartition("/")[0]
            full.add(f)
    groups = []

    def fill(parent, depth):
        subs = [f for f in every if f.rpartition("/")[0] == parent and f in full]
        for f in sorted(subs, key=lambda f: (f.lower(), f)):
            groups.append((f.rpartition("/")[2], depth, newest(shapes_in.get(f, ()))))
            fill(f, depth + 1)
    fill("", 0)
    return top, newest(shapes_in.get("", ())), groups


def choice_names(choices):
    """Every saved name of library_choices in the order the dropdown shows them (name_index's order after top)."""
    _, loose, groups = choices
    return list(loose) + [n for _, _, names in groups for n in names]


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


def load_layers(name, path=None):
    """load_drawing as drawn. path: that very file (a second file holding a name already used), not the one the
    name finds."""
    path, b = path or shape_path(name), built_in_name(name)
    strokes, areas = None, []
    if path:
        try:
            with open(path, encoding="utf-8-sig") as f:  # (read whole, then let go: a big file held open while
                text = f.read()                            # decoded blocked moving it, hunt 2026-11-24)
            got = json.loads(text)
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


def _free_file(where, stem, keep=None):
    """where/stem.json, or "stem (2).json"... when that file is taken. keep: the shape's own file now (it may stay
    that one, e.g. only upper / lower case changed)."""
    path, k = os.path.join(where, stem + ".json"), 2
    while os.path.exists(path) and os.path.normcase(path) != os.path.normcase(keep or ""):
        path, k = os.path.join(where, f"{stem} ({k}).json"), k + 1
    return path


def free_path(name, keep=None, folder=""):
    """A file for a shape called `name` in that library folder that no other shape uses ("x (2).json" if "x.json"
    is taken). keep: as in _free_file."""
    return _free_file(folder_path(folder), file_stem(name), keep)


def shape_text(name, strokes, areas=()):
    text = '{"name": ' + json.dumps(name) + ',\n "strokes": [\n  ' + ",\n  ".join(json.dumps(st) for st in strokes)
    text += "\n]"
    if areas:
        text += ',\n "areas": ' + json.dumps([[round(u, 6), round(v, 6), c] for u, v, c in areas])
    return text + "}\n"


def save_shape(name, strokes, areas=(), folder=""):
    """Saves a library shape: over the one with that name if any (in ITS folder), else a new file in `folder`.
    Returns its file."""
    path = shape_path(name)
    if not path:
        path = free_path(name, folder=folder)
        long_check(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_text(path, shape_text(name, strokes, areas))
    return path


def rename_shape(old, new, path=None):
    """A saved shape gets a new name (its file too, when it can; it stays in its folder). path: that very file (a
    second file holding a name already used). Raises OSError; ValueError if it can't be read."""
    path = path or shape_path(old)
    strokes, areas = load_layers(old, path)
    if not path or strokes is None:
        raise ValueError(old)
    to = _free_file(os.path.dirname(path), file_stem(new), keep=path)
    long_check(to)
    write_text(to, shape_text(new, strokes, areas))
    if os.path.normcase(to) != os.path.normcase(path):
        os.remove(path)


def move_shape(path, folder):
    """A saved shape's file moved into that library folder (its file name kept, " (2)" when taken there). Returns
    the new file. Raises OSError (FileNotFoundError: the folder is gone)."""
    where = folder_path(folder)
    if os.path.normcase(os.path.dirname(path)) == os.path.normcase(where):
        return path
    if not os.path.isdir(where):
        raise FileNotFoundError(where)
    to = _free_file(where, os.path.basename(path)[:-5])
    long_check(to)
    _retried(os.rename, path, to)  # (never over a file: _free_file found a free name)
    return to


IN_USE_RETRY_S = 0.3  # (a file another program holds for a moment, e.g. a shape picture being read: tried again)


def _retried(fn, *args):
    """fn(*args), once more after IN_USE_RETRY_S when Windows says the file is in use."""
    try:
        return fn(*args)
    except PermissionError:
        import time
        time.sleep(IN_USE_RETRY_S)
        return fn(*args)


PATH_MOST = 259  # (Windows' path limit without its long-path setting: 260 with the ending)


class PathTooLong(OSError):
    """A new / renamed / moved file or folder would pass Windows' path limit (its text says so)."""

    def __str__(self):
        return tr("drawer.path_too_long")


def long_check(path, inside=0):
    """Raises PathTooLong when `path` (plus the longest path `inside` it adds) passes Windows' limit and long
    paths aren't on (hunt 2026-11-24: deeper folders were skipped silently, their shapes gone from every list)."""
    from files import system
    if len(os.path.abspath(path)) + inside > PATH_MOST and not system.long_paths():
        raise PathTooLong(path)


def _inside_most(path):
    """The longest path under a folder, counted from the folder itself (0: empty / not there)."""
    most = 0
    for root, dirs, files in os.walk(path):
        for f in files + dirs:
            most = max(most, len(os.path.join(root, f)) - len(path))
    return most


# ---------------------------------------------------------------- folders
# Folder names ARE their names on disk (unlike shapes): characters Windows refuses are REFUSED, not swapped (user
# 2026-11-24). folder_problem says why; the window shows it and keeps its box open.

def folder_problem(name):
    """None when `name` can be a folder's name, else what's wrong: "empty", "chars" (one of BAD_CHARS or a hidden
    character), "end" (ends with a dot or a space), "device" (a name Windows keeps for itself), "long"."""
    if not name.strip():
        return "empty"
    if any(c in BAD_CHARS or c < " " for c in name):
        return "chars"
    if name.rstrip(". ") != name:
        return "end"
    if name.split(".")[0].strip().upper() in DEVICES:
        return "device"
    if len(name) > NAME_MAX:
        return "long"
    return None


def _check_name(name):
    name = name.strip()
    problem = folder_problem(name)
    if problem:
        raise ValueError(problem)
    return name


def make_folder(name, parent=""):
    """A new folder called `name` inside `parent` ("" = the top). Returns its folder ("A/name"). Raises ValueError
    (folder_problem's word), FileExistsError (one with that name is there), OSError."""
    name = _check_name(name)
    rel = f"{parent}/{name}" if parent else name
    long_check(folder_path(rel))
    os.makedirs(folder_path(parent), exist_ok=True)
    os.mkdir(folder_path(rel))
    return rel


def rename_folder(folder, name):
    """A folder gets a new name, in the same place (only upper / lower case changed works too). Returns its new
    folder. Raises like make_folder."""
    name = _check_name(name)
    parent = folder.rpartition("/")[0]
    rel = f"{parent}/{name}" if parent else name
    src, dst = folder_path(folder), folder_path(rel)
    if os.path.exists(dst) and os.path.normcase(dst) != os.path.normcase(src):
        raise FileExistsError(dst)
    long_check(dst, _inside_most(src))
    _retried(os.rename, src, dst)
    return rel


def move_folder(folder, into):
    """A folder (and all it holds) moved into another folder ("" = the top). Returns its new folder. Raises
    ValueError("inside") when `into` is that folder or one inside it, FileExistsError, OSError."""
    if into == folder or into.startswith(folder + "/"):
        raise ValueError("inside")
    name = folder.rpartition("/")[2]
    if folder.rpartition("/")[0] == into:
        return folder
    rel = f"{into}/{name}" if into else name
    if os.path.exists(folder_path(rel)):
        raise FileExistsError(folder_path(rel))
    long_check(folder_path(rel), _inside_most(folder_path(folder)))
    _retried(os.rename, folder_path(folder), folder_path(rel))
    return rel


def folder_count(folder):
    """(saved shapes, sub folders) inside a folder, all the way down: the Delete question says them."""
    files, folders = _scan()
    inside = folder + "/"
    shapes = sum(1 for _, _, f, _ in files if f == folder or f.startswith(inside))
    return shapes, sum(1 for f in folders if f.startswith(inside))


def delete_folder(folder, for_good=False):
    """A folder and all it holds deleted: to the Recycle Bin / trash (user 2026-11-24); for_good: deleted outright
    (only after the window warned that there is no trash). True = gone; False = no trash here (nothing done: ask,
    then call again with for_good); None = the system's own question was cancelled. Raises OSError."""
    from files import system
    path = folder_path(folder)
    if for_good:
        import shutil
        shutil.rmtree(path)
        return True
    return system.to_trash(path)


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
