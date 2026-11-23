"""Files Spiderweb remembers (QoL stage E, user 2026-11-23): File → Open recent (projects + loaded MIDI files, 16
each, newest first), the folder the Open / Load MIDI pickers start in (one per kind), and the backups Save keeps of
a project file before replacing it (user\\backups\\project, one folder per project; a few per project + a size cap
over them all, both in Settings → General)."""

import filecmp
import hashlib
import json
import os
import shutil
import time

from files.about import USER
from files.safefile import write_text

RECENT_FILE = os.path.join(USER, "recent.json")
BACKUPS = os.path.join(USER, "backups", "project")
RECENT_MOST = 16  # entries per list (user)
KINDS = ("projects", "midi")
BACKUP_COUNT = (0, 100, 5)  # backups kept per project: least (0 = none), most, default (user)
BACKUP_GB = (0.1, 1000.0, 1.0)  # every project's backups together: least, most, default in GB (user)

_data = None


def file_stamp(path):
    """(size, modified time) of a file, None when it can't be read: tells a file is as it was."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _load():
    """The saved lists, read forgivingly (anything odd = left out)."""
    global _data
    if _data is not None and _data.get("_file") == RECENT_FILE:
        return _data
    data = {"_file": RECENT_FILE, "folders": {}}
    try:
        with open(RECENT_FILE, encoding="utf-8-sig") as f:
            got = json.load(f)
    except (OSError, ValueError):
        got = {}
    got = got if isinstance(got, dict) else {}
    for kind in KINDS:
        paths = got.get(kind)
        data[kind] = [p for p in paths if isinstance(p, str) and p][:RECENT_MOST] if isinstance(paths, list) else []
    folders = got.get("folders")
    if isinstance(folders, dict):
        data["folders"] = {k: v for k, v in folders.items() if k in KINDS and isinstance(v, str) and v}
    _data = data
    return data


def _save():
    data = _load()
    try:
        os.makedirs(os.path.dirname(RECENT_FILE), exist_ok=True)
        write_text(RECENT_FILE, json.dumps({k: v for k, v in data.items() if k != "_file"}, indent=1))
    except OSError:
        pass  # (only a convenience: the lists stay for this session)


def items(kind):
    return list(_load()[kind])


def add(kind, path):
    """path opened / saved: first in its list (once), its folder the picker's next start."""
    if not path:
        return
    path = os.path.normpath(os.path.abspath(path))
    data = _load()
    data[kind] = [path] + [p for p in data[kind] if not _same(p, path)][:RECENT_MOST - 1]
    data["folders"][kind] = os.path.dirname(path)
    _save()


def remove(kind, path):
    data = _load()
    data[kind] = [p for p in data[kind] if not _same(p, path)]
    _save()


def clear(kind):
    _load()[kind] = []
    _save()


def folder(kind, fallback):
    """Where the Open / Load MIDI picker starts: the last folder used for that kind while it's still there."""
    there = _load()["folders"].get(kind)
    return there if there and os.path.isdir(there) else fallback


def backup_folder(path):
    """This project's own backups folder: its name + a short mark of where it lives (two "song.spwb" in different
    folders keep apart)."""
    stem = os.path.splitext(os.path.basename(path))[0] or "project"
    where = hashlib.sha1(os.path.normcase(os.path.dirname(os.path.abspath(path))).encode("utf-8")).hexdigest()[:6]
    return os.path.join(BACKUPS, f"{stem} ({where})")


def _backups(folder_path):
    """[(modified time, size, path)] of one project's backups, oldest first."""
    out = []
    try:
        names = os.listdir(folder_path)
    except OSError:
        return out
    for name in names:
        p = os.path.join(folder_path, name)
        if name.endswith(".tmp") or not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append((st.st_mtime_ns, st.st_size, p))
    out.sort()
    return out


def back_up(path, keep, most_gb):
    """Before Save replaces the project file at path: a copy of the OLD file into its backups folder, unless keep is
    0, the file isn't there, or the newest backup is the same. Then the oldest ones go: past keep for this
    project, then (every project's together) past most_gb, never the copy just made. Returns the copy's path or
    None. Never raises: a backup that fails must not stop the save."""
    try:
        keep = int(keep)
        if keep <= 0 or not os.path.isfile(path):
            return None
        own = backup_folder(path)
        os.makedirs(own, exist_ok=True)
        have = _backups(own)
        if have and have[-1][1] == os.path.getsize(path) and filecmp.cmp(have[-1][2], path, shallow=False):
            made = None  # (saved again without changes: the newest backup already holds it)
        else:
            stem, ext = os.path.splitext(os.path.basename(path))
            made = os.path.join(own, f"{stem} {time.strftime('%Y-%m-%d %H.%M.%S')}{ext}")
            n = 2
            while os.path.exists(made):  # (two saves in one second)
                made = os.path.join(own, f"{stem} {time.strftime('%Y-%m-%d %H.%M.%S')} ({n}){ext}")
                n += 1
            try:
                shutil.copyfile(path, made + ".tmp")  # (a full disk leaves no half copy)
                os.replace(made + ".tmp", made)
            except OSError:
                try:  # (a copy failing partway: its half file goes too, hunt 2026-11-23)
                    os.remove(made + ".tmp")
                except OSError:
                    pass
                raise
            have = _backups(own)
        for _, _, p in have[:max(0, len(have) - keep)]:
            _remove(p)
        trim(most_gb, spare=made)
        return made
    except OSError:
        return None


def trim(most_gb, spare=None):
    """Every project's backups together no bigger than most_gb: oldest first (spare = never that one)."""
    every = []
    try:
        names = os.listdir(BACKUPS)
    except OSError:
        return
    for name in names:
        every += _backups(os.path.join(BACKUPS, name))
    every.sort()
    total, most = sum(s for _, s, _ in every), float(most_gb) * 1e9
    for _, size, p in every:
        if total <= most:
            break
        if spare is not None and _same(p, spare):
            continue
        if _remove(p):
            total -= size


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        return False
    try:  # (a project's folder left empty goes too)
        os.rmdir(os.path.dirname(p))
    except OSError:
        pass
    return True
