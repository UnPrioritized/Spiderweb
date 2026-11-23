"""The drawer's shape library list with sub folders (PLAN.md, user 2026-11-24): what each row is, folder rows with
▸ / ▾ (open ones remembered in settings.ini), picking, which folder a new shape is saved into."""

import json
from tkinter import font as tkfont

from files import settings
from files.lang import tr
from window import look
from window.drawer_library import BUILT_IN, library_entries, library_folders, shape_folder

INDENT = "    "  # (one folder level deeper)
OPEN, SHUT = "▾ ", "▸ "


def parent_of(folder):
    return folder.rpartition("/")[0]


def list_rows(open_folders):
    """The list's rows, top to bottom: built-ins (a saved shape with a built-in's name takes its place, user), then
    the folders A-Z (in each: its sub folders, then its shapes A-Z; shut ones hide what they hold), then the loose
    shapes A-Z. Each row: {"kind": "built" / "shape" / "folder", "name", "folder" (a shape's folder; a folder row's
    own), "path", "same" (library_entries), "depth"}."""
    entries = library_entries()
    folders = library_folders()
    rows, used = [], set()
    firsts = {e["name"].lower(): e for e in entries if e["same"] is None}
    for b in BUILT_IN:
        e = firsts.get(b.lower())
        if e:
            used.add(e["path"])
            rows.append(dict(e, kind="shape", depth=0))
        else:
            rows.append({"kind": "built", "name": b, "folder": "", "path": None, "same": None, "depth": 0})
    shapes_in, subs_in = {}, {}
    for e in entries:
        if e["path"] not in used:
            shapes_in.setdefault(e["folder"], []).append(e)
    for f in folders:
        subs_in.setdefault(parent_of(f), []).append(f)

    def fill(folder, depth):
        for f in sorted(subs_in.get(folder, ()), key=lambda f: (f.lower(), f)):
            rows.append({"kind": "folder", "name": f.rpartition("/")[2], "folder": f, "path": None, "same": None,
                         "depth": depth})
            if f in open_folders:
                fill(f, depth + 1)
        for e in sorted(shapes_in.get(folder, ()), key=lambda e: (e["name"].lower(), e["name"], e["path"])):
            rows.append(dict(e, kind="shape", depth=depth))

    fill("", 0)
    return rows


def row_text(row, open_folders):
    lead = INDENT * row["depth"]
    if row["kind"] == "folder":
        return lead + (OPEN if row["folder"] in open_folders else SHUT) + row["name"]
    return lead + row["name"]


class LibraryList:
    """Drawer mixin: the library list (self.listbox, made in Drawer.__init__)."""

    def load_open_folders(self):
        try:
            got = json.loads(settings.get("view", "library_open_folders", "") or "[]")
            self.open_folders = {f for f in got if isinstance(f, str)}
        except ValueError:
            self.open_folders = set()

    def save_open_folders(self):
        """The open folders kept for the next start (gone folders dropped)."""
        self.open_folders &= set(library_folders())
        try:
            settings.put_many({("view", "library_open_folders"): json.dumps(sorted(self.open_folders),
                                                                            ensure_ascii=False)})
        except OSError:
            pass  # (settings.ini can't be written: remembered until the drawer closes)

    def refresh_list(self, select=None):
        """Fills the list again. select: a shape's name to pick (its folders opened so it shows)."""
        self.end_rename(False)
        if not hasattr(self, "open_folders"):
            self.load_open_folders()
        if select:
            f = shape_folder(select)
            opened = False
            while f:
                opened = opened or f not in self.open_folders
                self.open_folders.add(f)
                f = parent_of(f)
            if opened:
                self.save_open_folders()
        self.fill_rows(lambda r: select and r["kind"] != "folder" and r["same"] is None
                       and r["name"].lower() == select.lower())
        self.app.refresh_custom_names()

    def fill_rows(self, pick=None):
        """The rows made again from the library; pick(row) = rows to pick (the first one is scrolled to)."""
        lb = self.listbox
        self.peek.show(None, lb, 0)  # (the rows change: a picture of a gone / moved row goes, hunt 11-23)
        self.rows = list_rows(self.open_folders)
        lb.delete(0, "end")
        for r in self.rows:
            lb.insert("end", row_text(r, self.open_folders))
        for i, r in enumerate(self.rows):
            if r["same"] is not None:
                lb.itemconfig(i, foreground=look.WARN)
        seen = False
        for i, r in enumerate(self.rows):
            if pick and pick(r):
                lb.selection_set(i)
                if not seen:
                    lb.see(i)
                    seen = True
        self.sync_library()

    def row_tip(self, i):
        """A marked row's tip: another shape has its name (rename one)."""
        r = self.rows[i] if 0 <= i < len(getattr(self, "rows", ())) else None
        if r and r["same"] is not None:
            return tr("drawer.same_name", name=r["name"], folder=r["same"] or tr("drawer.top_folder"))
        return ""

    def row_shape(self, i):
        """The name the picture under the mouse shows (None: a folder row, or a marked one)."""
        r = self.rows[i] if 0 <= i < len(getattr(self, "rows", ())) else None
        return r["name"] if r and r["kind"] != "folder" and r["same"] is None else None

    def picked_rows(self):
        return [self.rows[i] for i in self.listbox.curselection() if i < len(self.rows)]

    def picked_row(self):
        """The one row picked (None when none or several are)."""
        rows = self.picked_rows()
        return rows[0] if len(rows) == 1 else None

    def picked(self):
        """The one shape picked in the library list (None when none, several or a folder are)."""
        r = self.picked_row()
        return r["name"] if r and r["kind"] != "folder" else None

    def picked_all(self):
        return [r["name"] for r in self.picked_rows() if r["kind"] != "folder"]

    def save_folder(self):
        """Where a NEW shape is saved (user 2026-11-24): the picked folder, or the folder of the picked shape;
        nothing picked (or rows in different folders) = the top."""
        got = {r["folder"] for r in self.picked_rows()}
        return got.pop() if len(got) == 1 else ""

    def sync_library(self):
        """Open / Rename greyed while several shapes are picked (their tips say why)."""
        state = ["disabled"] if len(self.listbox.curselection()) > 1 else ["!disabled"]
        for key in ("open", "rename"):
            self.lib_buttons[key].state(state)

    def toggle_folder(self, row, on=None):
        """A folder row opened / shut (on None = the other way); the list keeps that folder row picked."""
        f = row["folder"]
        if on is None:
            on = f not in self.open_folders
        if on == (f in self.open_folders):
            return
        (self.open_folders.add if on else self.open_folders.discard)(f)
        self.save_open_folders()
        top = self.listbox.yview()[0]
        self.fill_rows(lambda r: r["kind"] == "folder" and r["folder"] == f)
        self.listbox.yview_moveto(top)
        self.listbox.see(next(i for i, r in enumerate(self.rows) if r["kind"] == "folder" and r["folder"] == f))

    def on_arrow(self, i, x):
        """True when x (in the list) is on folder row i's ▸ / ▾."""
        r = self.rows[i] if 0 <= i < len(self.rows) else None
        if not r or r["kind"] != "folder":
            return False
        font = tkfont.Font(font=self.listbox.cget("font"))
        lead = font.measure(INDENT * r["depth"])
        box = self.listbox.bbox(i)
        x0 = (box[0] if box else 0) + lead
        return x0 - 2 <= x <= x0 + font.measure(OPEN) + 2

    def list_key_side(self, way):
        """Right = open the picked folder; Left = shut it, or from a shape inside: up to its folder (like File
        Explorer's tree)."""
        r = self.picked_row()
        if not r:
            return
        if r["kind"] == "folder" and (way > 0) != (r["folder"] in self.open_folders):
            self.toggle_folder(r, way > 0)
        elif way < 0 and r["depth"] > 0:
            f = r["folder"] if r["kind"] != "folder" else parent_of(r["folder"])
            lb = self.listbox
            i = next(i for i, q in enumerate(self.rows) if q["kind"] == "folder" and q["folder"] == f)
            lb.selection_clear(0, "end")
            lb.selection_set(i)
            lb.activate(i)
            lb.see(i)
            self.sync_library()
