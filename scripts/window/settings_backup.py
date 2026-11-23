"""Settings backup on update (user 2026-11-08): the first start of a new version (not the very first start) packs
the old version's app settings and the user's own saved presets into ONE compressed zip,
user/backups/settings/<old version>-<its commit>.zip (user 2026-11-14; was a copied folder; settings/ since
2026-11-23, older ones moved there by move_old), before this version saves
anything over them, and says so in a small window of its own (then What's new opens). Projects, autosaves, logs and
saved MIDI indexes are left alone. Every backup is kept (user's pick: never deleted). Read only, nothing is moved.
A file that can't be read is logged; with nothing packed, nothing is said."""

import io
import os
import re
import sys
import tkinter as tk
import zipfile
from tkinter import ttk

from files import safefile, settings, system
from files.errors import write_log
from files.lang import tr
from window import look


def _sources():
    """(name in the backup, path) of every file / folder holding settings or the user's own presets. Read from each
    module's own path, so tests (which point them elsewhere) back up their own. New saved settings: add them here."""
    from files import colour_list, shape_defaults
    from window import chop_window, drawer, hz_presets, hz_trace, palettes, pattern_dialog, stir_presets
    paths = (settings.SETTINGS_FILE, look.LOOK_FILE, palettes.FOLDER, drawer.LIBRARY, hz_presets.PRESETS_FILE,
             stir_presets.PRESETS_FILE, shape_defaults.DEFAULTS_FILE,
             hz_trace.SHAPES_FILE, pattern_dialog.PATTERNS_FILE, pattern_dialog.OLD_CURVES_FILE,
             chop_window.RHYTHMS_FILE, colour_list.FORMATS_FILE)
    return [(os.path.basename(p), p) for p in paths]


def folder():
    """user/backups/settings (next to settings.ini; user 2026-11-23: its own folder beside backups/project)."""
    return os.path.join(os.path.dirname(settings.SETTINGS_FILE), "backups", "settings")


OLD_NAME = re.compile(r"^\d+\.\d+")  # (a backup made before they had their own folder: "1.7.2-5ec9217(.zip)")


def move_old():
    """Settings backups left in user/backups itself (older versions) moved into its settings folder, once; one that
    can't be moved stays where it is."""
    top = os.path.dirname(folder())
    try:
        names = [n for n in os.listdir(top) if OLD_NAME.match(n)]
    except OSError:
        return
    for n in names:
        try:
            os.makedirs(folder(), exist_ok=True)
            if not os.path.exists(os.path.join(folder(), n)):
                os.replace(os.path.join(top, n), os.path.join(folder(), n))
        except OSError:
            pass


def label(version, build=""):
    """'1.7.2-5ec9217': the version + its build's short commit (build stamp "5ec9217, 2026-11-06"; "+" = changed
    files, dropped), the version alone when the build isn't known."""
    commit = re.sub(r"[^0-9A-Za-z]", "", str(build or "").split(",")[0])
    name = re.sub(r"[^0-9A-Za-z._-]", "_", str(version)) or "old"
    return f"{name}-{commit}" if commit else name


def _files(name, path):
    """(name inside the zip, path) of a source file, or of every file inside a source folder."""
    if os.path.isfile(path):
        return [(name, path)]
    found = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(root, f)
            found.append(("/".join([name] + os.path.relpath(full, path).split(os.sep)), full))
    return found


def make(version, build=""):
    """Packs them into one compressed zip (user 2026-11-14; " (2)" ... when the name is taken, by an older backup
    folder too). Returns the zip, None when nothing was packed (nothing there, or every read failed: logged)."""
    move_old()
    base = os.path.join(folder(), label(version, build))
    target, n = base, 1
    while os.path.exists(target) or os.path.exists(target + ".zip"):
        n += 1
        target = f"{base} ({n})"
    target += ".zip"
    data, packed = io.BytesIO(), 0
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        for name, path in _sources():
            for inside, full in (_files(name, path) if os.path.exists(path) else ()):
                try:
                    with open(full, "rb") as f:
                        z.writestr(zipfile.ZipInfo.from_file(full, inside, strict_timestamps=False), f.read(),
                                   zipfile.ZIP_DEFLATED)
                    packed += 1
                except OSError:
                    write_log(*sys.exc_info(), "(backing up the older version's settings)")
    if not packed:
        return None
    try:
        os.makedirs(folder(), exist_ok=True)
        safefile.write_bytes(target, data.getvalue())
    except OSError:
        write_log(*sys.exc_info(), "(backing up the older version's settings)")
        return None
    return target


def shown_path(path):
    """The backup as the user knows it: from the user folder on ("user/backups/settings/1.7.2-5ec9217.zip")."""
    user = os.path.dirname(os.path.dirname(os.path.dirname(path)))
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
        system.show_file(self.path)

    def close(self):
        then, self.then = self.then, None
        self.destroy()
        if then:
            then()
