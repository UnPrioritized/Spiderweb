"""The user's own saved lists as ONE FILE PER ITEM (user 2026-11-24, PLAN.md "Presets as one file each": deleting
one should go to the Recycle Bin like shapes and palettes). Each list = a folder user/presets/<list>/ of .json files,
each holding one item with its "name" inside (any character; the file is named after it by file_stem, " (2)" when
taken), like the shape library.
- Order = file time, OLDEST FIRST (as the old list files were: newest last); saving over an item rewrites its file,
  so it becomes the newest. Lists show them newest first.
- Read every time a list is asked for (a file added / deleted in File Explorer shows when the list opens).
- A file that can't be read, or whose item the list can't use (a newer Spiderweb's?), is left out and left alone.
- The old list file of 1.7.7 and before (user/hz_presets.json {"presets": [...]}) is split into files the first time
  the list is read, then renamed "-old" (hz_presets-old.json; user). An old file that can't be read is renamed
  "-broken" (nothing lost). Same names: the old file's item is written over the folder's when the folder's file is
  OLDER than the old list (an older Spiderweb started it again after the split), never over a save made since."""

import json
import os
import time

from files import about
from files.safefile import write_text

BAD_ENTRY = (TypeError, ValueError, KeyError, AttributeError, IndexError)  # (what reading a damaged entry raises)
SPLIT_STEP_S = 2.0  # seconds between the file times of a split list's items (keeps their order; FAT has 2 s steps)
LISTS = []  # every Folder made (split_old splits all lists sharing one old file)
_SPLIT_THIS_RUN = set()  # old list files split this run that couldn't be renamed (held): not split again till restart


ROOT = None  # (tests: a folder of their own for the lists ...)
OLD_DIR = None  # (... and for the old list files; None = user/)


def root():
    """user/presets."""
    return ROOT or os.path.join(about.USER, "presets")


class Folder:
    """One saved list. sub: its folder's name under user/presets; old: the 1.7.7 list file's name in user/ and the
    key its list is under (None = never had one); what: the word for a device-name file ("CON (preset)")."""

    def __init__(self, sub, old=None, key=None, what="preset", ensure_ascii=False):
        self.sub, self.old, self.key, self.what, self.ensure_ascii = sub, old, key, what, ensure_ascii
        LISTS.append(self)

    def path(self):
        return os.path.join(root(), self.sub)

    def old_path(self):
        return os.path.join(OLD_DIR or about.USER, self.old) if self.old else None

    # ---------------------------------------------------------------- reading

    def files(self):
        """[(file time, path)] of every .json file in the folder, oldest first (none: [])."""
        self.split_old()
        out = []
        try:
            with os.scandir(self.path()) as it:
                for e in it:
                    if e.name.lower().endswith(".json") and e.is_file():
                        try:
                            out.append((e.stat().st_mtime, e.name.casefold(), e.path))
                        except OSError:
                            pass
        except OSError:
            return []
        out.sort()
        return [(t, p) for t, _, p in out]

    @staticmethod
    def read(path):
        """The item in a file (a dict with a non-empty "name"), None when it can't be read."""
        try:
            with open(path, encoding="utf-8-sig") as f:
                got = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(got, dict) or not isinstance(got.get("name"), str) or not got["name"].strip():
            return None
        return got

    def found(self, clean):
        """[(path, item)] oldest first: each readable file's item through clean(item) (raising BAD_ENTRY or giving
        nothing = left out). Two files with one name (copied by hand): both listed, the newer one last."""
        out = []
        for _, path in self.files():
            raw = self.read(path)
            if raw is None:
                continue
            try:
                got = clean(raw)
            except BAD_ENTRY:
                got = None
            if got:
                out.append((path, got))
        return out

    def entries(self, clean):
        """The items oldest first (newest last), as the old list files had them."""
        return [item for _, item in self.found(clean)]

    def paths_of(self, name, exact=False):
        """The files holding an item with this name (capitals or not; exact: only exactly it), oldest first."""
        low = name.strip().casefold()
        out = []
        for _, path in self.files():
            raw = self.read(path)
            if raw is None:
                continue
            n = raw["name"].strip()
            if (n == name.strip()) if exact else (n.casefold() == low):
                out.append(path)
        return out

    # ---------------------------------------------------------------- writing

    def text(self, item):
        """An item as its file's text: the name first."""
        item = dict(item)
        name = item.pop("name")
        return json.dumps({"name": name, **item}, indent=1, ensure_ascii=self.ensure_ascii) + "\n"

    def free_path(self, name, keep=None):
        """folder/<file_stem>.json, " (2)"... when another file has it. keep: a file the item may stay in."""
        from window.drawer_library import file_stem
        stem, k = file_stem(name, self.what), 2
        path = os.path.join(self.path(), stem + ".json")
        while os.path.exists(path) and os.path.normcase(path) != os.path.normcase(keep or ""):
            path, k = os.path.join(self.path(), f"{stem} ({k}).json"), k + 1
        return path

    def save(self, item):
        """Writes item (with its "name", cut to NAME_MAX letters) as the newest: over the newest file already holding
        that name (capitals or not; its file renamed when the name's capitals changed); any OTHER file with the name
        (a copy made in File Explorer) goes to the Recycle Bin (the old lists kept one item per name; hunt 11-24:
        it was deleted outright). Returns its path. OSError when it can't be written (PathTooLong past Windows'
        path limit)."""
        from window.drawer_library import clean_name, long_check
        item = dict(item, name=clean_name(item["name"]))
        name = item["name"]
        had = self.paths_of(name)
        os.makedirs(self.path(), exist_ok=True)
        keep = had[-1] if had else None
        path = self.free_path(name, keep)
        long_check(path)
        write_text(path, self.text(item))
        now = time.time()
        try:  # (newest even when the clock / file system rounds: past every other file's time)
            newest = max((t for t, p in self.files() if os.path.normcase(p) != os.path.normcase(path)), default=0.0)
            if newest >= os.path.getmtime(path):
                os.utime(path, (now, newest + 0.01))
        except OSError:
            pass
        from files import system
        for p in had:
            if os.path.normcase(p) == os.path.normcase(path):
                continue
            try:
                if p == keep:  # (the item's own file, renamed for new capitals)
                    os.remove(p)
                elif not system.to_trash(p):
                    pass  # (no bin here: the copy stays; the newest one is the one listed first)
            except OSError:
                pass
        return path

    def delete(self, name, ask_for_good):
        """The NEWEST file holding exactly name (else capitals or not) to the Recycle Bin (system.bin_or_delete: no bin
        here = ask_for_good() first; a copy made in File Explorer stays, hunt 11-24). True = gone (or already gone:
        deleted outside, hunt 11-24), False = kept (cancelled). Raises system.BinError (in use: nothing deleted) /
        OSError."""
        from files import system
        had = self.paths_of(name, exact=True) or self.paths_of(name)
        if not had:
            return True
        return bool(system.bin_or_delete(had[-1], ask_for_good))

    # ---------------------------------------------------------------- the old list file

    def split_old(self):
        """The 1.7.7 list file split into files once (see the docstring). Never raises. Hunt 11-24: an item whose file
        can't be written is SKIPPED (left in the "-old" file), a folder file NEWER than the old list is never written
        over (a save since), two items differing only in capitals both kept (1.7.7's rhythms / formulas allowed
        it: the second gets " (2)"), and an old list that can't be renamed is split once per run only."""
        old = self.old_path()
        if not old or old in _SPLIT_THIS_RUN or not os.path.exists(old):
            return
        try:
            with open(old, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError
        except OSError:
            return  # (held by another program: next time)
        except ValueError:
            _aside(old, "-broken")
            return
        try:
            when = os.path.getmtime(old)
        except OSError:
            when = time.time()
        # every list kept in this file (patterns.json holds two), so renaming it loses none
        for lst in [f for f in LISTS if f.old == self.old] or [self]:
            items = data.get(lst.key)
            items = [p for p in (items if isinstance(items, list) else ())
                     if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip()]
            if not items:
                continue
            try:
                os.makedirs(lst.path(), exist_ok=True)
            except OSError:
                continue
            there = lst._names_there()  # (read ONCE: hunt 11-24, 400 items took 6 s re-reading per item)
            written = set()
            n = len(items)
            for i, item in enumerate(items):
                name = item["name"].strip()
                low = name.casefold()
                found = there.get(low, [])
                exact = [c for c in found if c[2] == name]  # (its own file from an earlier split, else a same-name one)
                had = exact[-1] if exact else (found[-1] if found and low not in written else None)
                if had and had[1] > when:  # (saved since the old list was written: keep the save)
                    continue
                try:
                    path = lst.free_path(name, had[0] if had else None)
                    write_text(path, lst.text(item))
                    t = when - (n - 1 - i) * SPLIT_STEP_S
                    os.utime(path, (t, t))
                except OSError:
                    continue  # (a name Windows refuses as a file: it stays in the "-old" file)
                written.add(low)
                if not had:
                    there.setdefault(low, []).append((path, t, name))
        _aside(old, "-old")
        if os.path.exists(old):  # (couldn't be renamed: held by another program; tried again next start)
            _SPLIT_THIS_RUN.add(old)

    def _names_there(self):
        """{name in lower case: [(file, its time, the name as written)] oldest first} of the folder now (split_old's
        lookups)."""
        out = {}
        try:
            with os.scandir(self.path()) as it:
                found = [(e.stat().st_mtime, e.path) for e in it if e.name.lower().endswith(".json") and e.is_file()]
        except OSError:
            return out
        for t, p in sorted(found):
            raw = self.read(p)
            if raw:
                n = raw["name"].strip()
                out.setdefault(n.casefold(), []).append((p, t, n))
        return out


def change(folder, name, item, parent, title):
    """A window's Save / Delete: item (a dict without the name) saved as name, or None = deleted (to the Recycle
    Bin; no bin here = asked "delete for good?"). What went wrong is said (title on the message). True when done."""
    from tkinter import messagebox
    from files import system
    from files.lang import tr
    try:
        if item is not None:
            folder.save({"name": name, **{k: v for k, v in item.items() if k != "name"}})
            return True
        return folder.delete(name, lambda: messagebox.askyesno(title, tr("presets.no_bin", name=name), parent=parent))
    except system.BinError:
        messagebox.showerror(title, tr("presets.bin_in_use", name=name), parent=parent)
    except OSError as e:
        messagebox.showerror(title, tr("presets.save_failed", path=folder.path(), e=e), parent=parent)
    return False


def bin_file(path, name, parent, title):
    """One item's own file (lists that tell same-named items apart by file) to the Recycle Bin, as change does it.
    A file already gone = done. True when gone."""
    from tkinter import messagebox
    from files import system
    from files.lang import tr
    if not os.path.exists(path):
        return True
    try:
        return system.bin_or_delete(path, lambda: messagebox.askyesno(title, tr("presets.no_bin", name=name),
                                                                       parent=parent))
    except system.BinError:
        messagebox.showerror(title, tr("presets.bin_in_use", name=name), parent=parent)
    except OSError as e:
        messagebox.showerror(title, tr("presets.save_failed", path=os.path.dirname(path), e=e), parent=parent)
    return False


# The lists (folder names are fixed English words: they're never translated, like "shapes" / "palettes")
HZ_PRESETS = Folder("Hz bass", "hz_presets.json", "presets")
STIR = Folder("Stir", "stir_presets.json", "presets")
WARP = Folder("Warp", "warp_presets.json", "presets")
RHYTHMS = Folder("Chop rhythms", "rhythms.json", "rhythms", what="rhythm")
PATTERNS = Folder("Formula patterns", "patterns.json", "patterns", what="pattern")
CURVE_SHAPES = Folder("Formula shapes", "patterns.json", "shapes", what="shape")
HZ_LINES = Folder("Hz shape lines", "hz_shapes.json", "shapes", what="line")
WOBBLES = Folder("Hz wobble waves", "hz_wobbles.json", "shapes", what="wave")
FORMATS = Folder("Colour formats", "colour_formats.json", "formats", what="format")


def _aside(path, tag):
    """path renamed "<name><tag>.json" ("<tag>-2"... when taken)."""
    base, ext = os.path.splitext(path)
    to, n = base + tag + ext, 1
    while os.path.exists(to):
        n += 1
        to = f"{base}{tag}-{n}{ext}"
    try:
        os.replace(path, to)
    except OSError:
        pass
    return to
