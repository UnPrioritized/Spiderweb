"""Stir presets (user 2026-11-16; a requester: a stir couldn't be reused in another project): named stirs kept in
spiderweb/user/stir_presets.json, picked / saved / deleted in a row above the Stir window's tabs (window/
stir_window.py). A preset = one stir (the shown tab) or every tab ("Save all tabs"); picking one puts its stirs IN
PLACE OF the shown tab only (1 tab -> n), the other tabs untouched. Centre / Area numbers are % of the colour box, so
they carry over to any shape."""

import json
import os
from tkinter import messagebox

from files.about import USER
from files.lang import tr
from files.safefile import write_text
from notes.recolour import MOST_STIRS, full_stir, one_stir, order_of, stirs_of, with_order

PRESETS_FILE = os.path.join(USER, "stir_presets.json")


def read_file():
    """The file's entries as they are ([] without a file), None when it can't be read (broken)."""
    if not os.path.exists(PRESETS_FILE):
        return []
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            items = json.load(f).get("presets", [])
        return items if isinstance(items, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def clean_stirs(stirs):
    """A preset's saved stirs -> [one_stir or {}] (1..MOST_STIRS), or None when there's nothing usable."""
    if not isinstance(stirs, list) or not stirs:
        return None
    out = [one_stir(s) or {} for s in stirs[:MOST_STIRS] if isinstance(s, dict)]
    return out or None


def load_presets():
    """The user's presets [{"name", "stirs"}] in the file's order (broken entries left out)."""
    out = []
    for p in read_file() or ():
        if isinstance(p, dict) and str(p.get("name", "")).strip():
            stirs = clean_stirs(p.get("stirs"))
            if stirs:
                out.append({"name": str(p["name"]).strip(), "stirs": stirs})
    return out


def same_name(p, name):
    """A file entry with this name (capitals or not: one preset)."""
    return isinstance(p, dict) and str(p.get("name", "")).strip().casefold() == name.casefold()


def change_presets(name, stirs, parent):
    """The preset `name` replaced by `stirs` (None: deleted; a new one goes last = newest), every other entry kept as
    it is. A file that can't be read is kept aside first (stir_presets-broken.json, said). False (and said) when the
    file can't be written."""
    items = read_file()
    try:
        if items is None:
            broken = os.path.splitext(PRESETS_FILE)[0] + "-broken.json"
            os.replace(PRESETS_FILE, broken)
            messagebox.showwarning(tr("stir.preset_save_title"), tr("stir.preset_file_broken", path=broken),
                                   parent=parent)
            items = []
        items = [p for p in items if not same_name(p, name)]
        if stirs is not None:
            items.append({"name": name, "stirs": stirs})
        write_text(PRESETS_FILE, json.dumps({"presets": items}, indent=1))
    except OSError as e:
        messagebox.showerror(tr("stir.preset_save_title"), tr("stir.preset_save_failed", path=PRESETS_FILE, e=e),
                             parent=parent)
        return False
    return True


def preset_label(p):
    """The name shown in the list: "Name (n tabs)" for a preset of several tabs."""
    n = len(p["stirs"])
    return tr("stir.preset_tabs", name=p["name"], n=n) if n > 1 else p["name"]


def preset_tabs(rc, i, n):
    """Tabs i .. i + n - 1 of Recolour rc, filled in (missing ones: plain stirs), to tell whether a preset is there."""
    stack = stirs_of(rc)
    return [stack[k] if k < len(stack) else full_stir(None) for k in range(i, i + n)]


def tab_count_after(rc, i, n):
    """How many Stir tabs rc has once a preset of n tabs is put in place of tab i."""
    return max(len(stirs_of(rc)), i + 1) - 1 + n


def with_preset(old, i, stirs, stirred_rc):
    """old's Recolour (clean or None) with its Stir tab i replaced by the preset's stirs (the stack padded with plain
    stirs up to tab i); the order of the colour steps written out in full: the preset's tabs take tab i's place
    (padded tabs and a tab not there yet go at the end, like "+")."""
    stack = stirs_of(old)
    had = len(stack)
    stack += [full_stir(None)] * (i + 1 - len(stack))
    put = [full_stir(s) for s in stirs]
    new_stack = stack[:i] + put + stack[i + 1:]
    o = order_of(old) if old else []
    if i < had:  # (tab i's own "stir" entry becomes n of them)
        seen = -1
        for j, k in enumerate(o):
            if k == "stir":
                seen += 1
                if seen == i:
                    o = o[:j] + ["stir"] * len(put) + o[j + 1:]
                    break
    else:
        o = o + ["stir"] * (len(new_stack) - had)
    rc = stirred_rc(old, new_stack, held=len(new_stack) if had else 0)  # (as with_stir: plain tabs on a shape with
    return with_order(rc, o) if rc else None  # no stir before = no stir)
