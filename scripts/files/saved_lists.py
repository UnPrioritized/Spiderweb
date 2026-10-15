"""The user's saved lists kept in one JSON file each (patterns + curve shapes, Chop rhythms, colour formats). Read
entry by entry: one that can't be read is left out of the list but stays in the file as it was (a newer Spiderweb
may have saved it). A file that can't be read at all is kept aside before a new one is started. So saving one entry
never loses the others."""

import json
import os

from files.safefile import write_text

BAD_ENTRY = (TypeError, ValueError, KeyError, AttributeError, IndexError)  # (what reading a damaged entry raises)


def _whole(path):
    """The file's contents as a dict ({} without the file); None when it's there but can't be read as one."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def entries(path, key, clean):
    """The readable entries under key, each through clean(entry) (raising BAD_ENTRY or giving None = left out)."""
    data = _whole(path) or {}
    items = data.get(key)
    out = []
    for item in items if isinstance(items, list) else ():
        try:
            got = clean(item)
        except BAD_ENTRY:
            got = None
        if got:
            out.append(got)
    return out


def change(path, key, drop, add=(), indent=1, ensure_ascii=True):
    """Saves the list under key: the entries whose name drop(name) says yes to are removed and add's go on the end,
    every other entry (and everything else in the file) kept as saved. A file that can't be read is kept aside first.
    Returns where it was kept (tell the user), else None. OSError when the file can't be written."""
    data, kept = _whole(path), None
    items = data.get(key, []) if data is not None else None
    if not isinstance(items, list):
        kept = keep_aside(path)
        data, items = {}, []
    data[key] = [p for p in items if not (isinstance(p, dict) and isinstance(p.get("name"), str) and drop(p["name"]))]
    data[key] += list(add)
    write_text(path, json.dumps(data, indent=indent, ensure_ascii=ensure_ascii))
    return kept


def keep_aside(path):
    """The damaged file moved to "<name>-broken.json" (-broken-2... when that's taken). Returns its path."""
    base, n = os.path.splitext(path)[0] + "-broken", 1
    kept = base + ".json"
    while os.path.exists(kept):
        n += 1
        kept = f"{base}-{n}.json"
    os.replace(path, kept)
    return kept
