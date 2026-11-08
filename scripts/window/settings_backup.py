"""Settings backup on update (user 2026-11-08): the first start of a new version (not the very first start) copies
the old version's app settings and the user's own saved presets into user/backups/<old version>-<its commit>/,
before this version saves anything over them, and says so in a small window of its own (then What's new opens).
Projects, autosaves, logs and saved MIDI indexes are left alone. Every backup is kept (user's pick: never deleted).
Copies only (shutil), nothing is moved. A copy that fails is logged; with nothing copied, nothing is said."""

import os
import re
import shutil
import sys
import tkinter as tk
from tkinter import ttk

from files import settings, system
from files.errors import write_log
from files.lang import tr
from window import look


def _sources():
    """(name in the backup, path) of every file / folder holding settings or the user's own presets. Read from each
    module's own path, so tests (which point them elsewhere) back up their own. New saved settings: add them here."""
    from files import colour_list
    from window import chop_window, drawer, hz_presets, hz_trace, palettes, pattern_dialog
    paths = (settings.SETTINGS_FILE, look.LOOK_FILE, palettes.FOLDER, drawer.LIBRARY, hz_presets.PRESETS_FILE,
             hz_trace.SHAPES_FILE, pattern_dialog.PATTERNS_FILE, pattern_dialog.OLD_CURVES_FILE,
             chop_window.RHYTHMS_FILE, colour_list.FORMATS_FILE)
    return [(os.path.basename(p), p) for p in paths]


def folder():
    """user/backups (next to settings.ini)."""
    return os.path.join(os.path.dirname(settings.SETTINGS_FILE), "backups")


def label(version, build=""):
    """'1.7.2-5ec9217': the version + its build's short commit (build stamp "5ec9217, 2026-11-06"; "+" = changed
    files, dropped), the version alone when the build isn't known."""
    commit = re.sub(r"[^0-9A-Za-z]", "", str(build or "").split(",")[0])
    name = re.sub(r"[^0-9A-Za-z._-]", "_", str(version)) or "old"
    return f"{name}-{commit}" if commit else name


def make(version, build=""):
    """Copies them into a new folder (" (2)" ... when the name is taken). Returns the folder, None when nothing was
    copied (nothing there, or every copy failed: logged)."""
    base = os.path.join(folder(), label(version, build))
    target, n = base, 1
    while os.path.exists(target):
        n += 1
        target = f"{base} ({n})"
    copied = 0
    for name, path in _sources():
        try:
            if os.path.isdir(path):
                if os.listdir(path):
                    shutil.copytree(path, os.path.join(target, name))
                    copied += 1
            elif os.path.isfile(path):
                os.makedirs(target, exist_ok=True)
                shutil.copy2(path, os.path.join(target, name))
                copied += 1
        except OSError:
            write_log(*sys.exc_info(), "(backing up the older version's settings)")
    return target if copied else None


def shown_path(path):
    """The backup folder as the user knows it: from the user folder on ("user/backups/1.7.2-5ec9217")."""
    user = os.path.dirname(os.path.dirname(path))
    try:
        return os.path.relpath(path, os.path.dirname(user))
    except ValueError:  # (another drive)
        return path


class BackupNote(tk.Toplevel):
    """'Settings from an older Spiderweb were found, a backup was made': Open folder / OK. then() runs when it
    closes (What's new opens after it, not over it)."""
    APP_ONLY = True  # (keys pressed here never change the shapes: App.app_only)

    def __init__(self, app, path, old, then=None):
        from window.updates import on_top
        super().__init__(app)
        self.path, self.then = path, then
        s = app.scale
        self.title(tr("backup.title"))
        box = ttk.Frame(self, padding=(16, 12, 16, 12))
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("backup.head"), font=look.font(10, "bold")).pack(anchor="w")
        self.text = tr("backup.text", old=old, folder=shown_path(path))
        ttk.Label(box, text=self.text, wraplength=int(400 * s), justify="left").pack(anchor="w", pady=(4, 12))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text=tr("backup.ok"), command=self.close).pack(side="right")
        ttk.Button(row, text=tr("backup.open_folder"), command=self.open_folder).pack(side="right", padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Return>", lambda e: self.close())
        on_top(self, app, "settings_backup")

    def open_folder(self):
        system.open_path(self.path)

    def close(self):
        then, self.then = self.then, None
        self.destroy()
        if then:
            then()
