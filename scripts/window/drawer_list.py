"""The drawer's shape library list with sub folders (PLAN.md, user 2026-11-24): what each row is, folder rows with
▸ / ▾ (open ones remembered in settings.ini), picking, which folder a new shape is saved into."""

import json
import tkinter as tk
from tkinter import font as tkfont, messagebox

from files import settings
from files.lang import tr, trn
from window import look
from window.drawer_library import BAD_CHARS, BUILT_IN, library_entries, library_folders, shape_folder

INDENT = "    "  # (one folder level deeper)
CATCH_UP_S = 0.5  # (catch_up: the library looked at again at most this often while the keyboard moves around)
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


def delete_error(e):
    """The message for a delete that failed: in use (the Recycle Bin move failed: nothing deleted) or another
    error."""
    import os
    from files.system import BinError
    if isinstance(e, BinError):
        return tr("drawer.bin_in_use", name=os.path.basename(str(e.filename or "")))
    return tr("drawer.couldn_t_delete", e=e)


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

    def catch_up(self, e=None):
        """The drawer has the keyboard again (hunt 2026-11-24: folders deleted / renamed in File Explorer kept their
        rows, opening one failed, saving into one made it again): the list made again when the library changed on
        disk, the same rows picked where they still are. At most every CATCH_UP_S."""
        import time
        now = time.perf_counter()
        if now - getattr(self, "_caught", 0.0) < CATCH_UP_S or self.renaming or getattr(self, "_drag", None):
            return
        self._caught = now
        if not hasattr(self, "rows"):
            return
        mark = lambda rs: [(r["kind"], r["folder"], r["path"], r["name"], r["same"]) for r in rs]
        if mark(list_rows(self.open_folders)) == mark(self.rows):
            return
        picked = {(r["kind"], r["folder"], r["path"]) for r in self.picked_rows()}
        top = self.listbox.yview()[0]
        self.fill_rows(lambda r: (r["kind"], r["folder"], r["path"]) in picked)
        self.listbox.yview_moveto(top)
        self.app.refresh_custom_names()

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
        import os
        from window.drawer_library import folder_path
        # (a row shown in a built-in's place at the top = the top, like a drop there: hunt 2026-11-24)
        got = {r["folder"] if r["kind"] == "folder" or r["depth"] else "" for r in self.picked_rows()}
        f = got.pop() if len(got) == 1 else ""
        return f if not f or os.path.isdir(folder_path(f)) else ""  # (gone since: never made again, hunt)

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
        at = next((i for i, r in enumerate(self.rows) if r["kind"] == "folder" and r["folder"] == f), None)
        if at is not None:  # (gone from the disk since the list was made: the list is simply up to date now)
            self.listbox.see(at)

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

    # -------------------------------------------------------- folders: new / rename / delete (stage C)

    def pick_folder(self, folder):
        """Fills the list again with that folder's row picked (its folders above opened)."""
        f, changed = parent_of(folder), False
        while f:
            changed = changed or f not in self.open_folders
            self.open_folders.add(f)
            f = parent_of(f)
        if changed:
            self.save_open_folders()
        self.fill_rows(lambda r: r["kind"] == "folder" and r["folder"] == folder)
        self.listbox.focus_set()

    def keep_fresh(self, do):
        """do(moves) moves files / folders, adding each (old path, new path) to moves. The drawing open here stays
        "saved" only when its name still finds ITS OWN file, moved (hunt 2026-11-24: a second file holding the name
        moved first in the order, and Save went over that other drawing without asking). A name that now finds
        another file is said on the status line."""
        import os
        from window.drawer_library import _saved, folder_of, shape_stamp
        before = {k: p for k, (_, p) in _saved().items()}
        name = self.saved_name
        fresh = bool(name) and not self.changed_elsewhere()
        moves = []
        got = do(moves)
        after = _saved()

        def moved(p):
            for old, new in moves:
                if os.path.normcase(p) == os.path.normcase(old):
                    return new
                if os.path.normcase(p).startswith(os.path.normcase(old) + os.sep):
                    return new + p[len(old):]
            return p
        same = lambda a, b: os.path.normcase(a) == os.path.normcase(b)
        if fresh:
            now = after.get(name.lower())
            if now and same(now[1], moved(before.get(name.lower(), ""))):
                self.saved_stamp = shape_stamp(name)
        switched = [(n, p) for k, (n, p) in after.items() if k in before and not same(moved(before[k]), p)]
        if switched:
            n, p = switched[0]
            self.app.status.config(text=tr("drawer.name_switched", name=n,
                                            folder=folder_of(p) or tr("drawer.top_folder")))
        return got

    def folder_moved(self, old, new):
        """Open folders under the old name follow the folder to its new name / place."""
        moved = {f for f in self.open_folders if f == old or f.startswith(old + "/")}
        self.open_folders = (self.open_folders - moved) | {new + f[len(old):] for f in moved}
        self.save_open_folders()

    def new_folder(self):
        """New folder (button / menu): "New folder" inside the picked folder (or the picked shape's), at the top when
        nothing is; its name box opens at once, like File Explorer."""
        from window.drawer_library import folder_path, make_folder
        import os
        self.end_rename(True)
        parent = self.save_folder()
        base, name, k = tr("drawer.new_folder_name"), None, 2
        name = base
        while os.path.exists(os.path.join(folder_path(parent), name)):
            name, k = f"{base} ({k})", k + 1
        try:
            made = make_folder(name, parent)
        except (OSError, ValueError) as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_make_folder", e=e), parent=self)
            return
        if parent:
            self.open_folders.add(parent)
        self.pick_folder(made)
        self.start_rename()

    def finish_folder_rename(self, row, typed, keep):
        """The rename box on a folder row closed: renamed. A name Windows refuses is REFUSED with the reason and the
        box opens again with what was typed (user 2026-11-24)."""
        from window.drawer_library import folder_path, folder_problem, rename_folder
        new = typed.strip()
        if not keep or not new or new == row["name"]:
            return
        problem = folder_problem(new)
        try:
            if problem:
                raise ValueError(problem)
            def do(moves):
                rel = rename_folder(row["folder"], new)
                moves.append((folder_path(row["folder"]), folder_path(rel)))
                return rel
            got = self.keep_fresh(do)
        except ValueError as e:
            why = tr(f"drawer.folder_{e}", name=new, chars=" ".join(BAD_CHARS))
        except FileExistsError:
            why = tr("drawer.folder_taken", name=new)
        except OSError as e:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_rename", e=e), parent=self)
            return
        else:
            self.folder_moved(row["folder"], got)
            self.pick_folder(got)
            self.app.refresh_custom_names()
            return
        messagebox.showerror(tr("drawer.spiderweb"), why, parent=self)
        self.after_idle(lambda: self.winfo_exists() and self.start_rename(typed))

    def delete_with_folders(self):
        """Delete with folder rows picked: ONE question (folders, the shapes inside, other picked shapes); folders go to
        the Recycle Bin (user 2026-11-24; none there = asked again, then deleted for good), picked shapes outside
        them are deleted like any shape."""
        import os
        from window.drawer_library import delete_folder, folder_count
        rows = self.picked_rows()
        folders = [r["folder"] for r in rows if r["kind"] == "folder"]
        folders = [f for f in folders if not any(f.startswith(o + "/") for o in folders)]  # (inside another: goes too)
        inside = lambda f: any(f == o or f.startswith(o + "/") for o in folders)
        loose = [r for r in rows if r["kind"] == "shape" and not inside(r["folder"])]
        shapes = sum(folder_count(f)[0] for f in folders)
        names = [e["name"] for e in library_entries() if inside(e["folder"])] + [r["name"] for r in loose]
        ask = (tr("drawer.delete_folder", name=folders[0]) if len(folders) == 1 else
               tr("drawer.delete_folders", n=len(folders)))
        ask += trn("drawer.delete_folder_shapes", shapes, n=shapes) if shapes else ""
        ask += trn("drawer.delete_folder_loose", len(loose), n=len(loose)) if loose else ""
        ask += tr("drawer.delete_folder_bin")
        if not messagebox.askyesno(tr("drawer.spiderweb"), ask, icon="warning", parent=self):
            return
        failed = None
        for f in folders:
            try:
                done = delete_folder(f)
                if done is False and messagebox.askyesno(tr("drawer.spiderweb"), tr("drawer.no_bin", name=f),
                                                         icon="warning", default="no", parent=self):
                    delete_folder(f, for_good=True)
            except OSError as e:
                failed = failed or e
        for r in loose:
            try:
                self.bin_shape(r)
            except OSError as e:
                failed = failed or e
        for name in names:
            self.shape_gone(name)
        if failed:
            messagebox.showerror(tr("drawer.spiderweb"), delete_error(failed), parent=self)
        self.app.sync_custom()
        self.save_open_folders()
        self.refresh_list()

    def bin_shape(self, row):
        """A shape's file to the Recycle Bin (user 2026-11-24); no bin here = asked, then deleted for good. True =
        gone. Raises OSError."""
        from files import system
        return system.bin_or_delete(row["path"], lambda: messagebox.askyesno(
            tr("drawer.spiderweb"), tr("drawer.no_bin_shape", name=row["name"]), icon="warning", default="no",
            parent=self))

    def list_menu(self, e):
        """Right-click on the list (THE CONTROL SCHEME: a menu, never deleting by itself): the row under the mouse
        picked first if it isn't; Open / Rename / Delete / New folder."""
        from window.widgets import popup_menu
        lb = self.listbox
        if self.drag_cancel():  # (right-click while dragging rows: the drag is off, no menu)
            return "break"
        self.end_rename(True)
        i = lb.nearest(e.y)
        box = lb.bbox(i) if i >= 0 else None
        if box and box[1] <= e.y < box[1] + box[3]:
            if i not in lb.curselection():
                lb.selection_clear(0, "end")
                lb.selection_set(i)
                lb.activate(i)
        else:
            lb.selection_clear(0, "end")
        self.sync_library()
        lb.focus_set()
        rows = self.picked_rows()
        m = tk.Menu(self, tearoff=0)
        one = rows[0] if len(rows) == 1 else None
        if one:
            if one["kind"] == "folder":
                label = tr("drawer.menu_shut") if one["folder"] in self.open_folders else tr("drawer.menu_open")
                m.add_command(label=label, accelerator=tr("drawer.menu_enter"), command=self.open_selected)
            else:
                m.add_command(label=tr("drawer.open"), accelerator=tr("drawer.menu_enter"), command=self.open_selected)
            m.add_command(label=tr("drawer.rename"), accelerator=tr("menu.key_f2"), command=self.start_rename,
                          state="disabled" if one["kind"] == "built" else "normal")
        if rows:
            m.add_command(label=tr("drawer.delete"), accelerator=tr("drawer.del"), command=self.delete_selected,
                          state="disabled" if all(r["kind"] == "built" for r in rows) else "normal")
            self.move_menu(m, rows)
            m.add_separator()
        m.add_command(label=tr("drawer.new_folder"), command=self.new_folder)
        popup_menu(m, lb, e)
        return "break"

    # -------------------------------------------------------- moving (stage D)

    DRAG_PX = 5  # (the mouse moves this far with the button held before rows are dragged)

    def drag_press(self, e, i):
        """A plain press on a row: maybe the start of dragging the picked rows. On a row already picked among several,
        the pick stays (like File Explorer) until the let go shows it was a click."""
        self._drag = None
        if e.state & 0x5:  # (Shift / Ctrl: picking, never dragging)
            return None
        lb = self.listbox
        keep = i in lb.curselection() and len(lb.curselection()) > 1
        self._drag = {"i": i, "y": e.y, "x": e.x, "on": False, "keep": keep, "target": None}
        if keep:
            lb.focus_set()
            lb.activate(i)
            lb.selection_anchor(i)
            return "break"
        return None

    def drop_target(self, y):
        """(row to light up or None, folder) the rows would be moved into at height y: a folder row = into it; a
        shape row = its folder (that folder's row lit, user 2026-11-24: a drop there lands in it); below the rows
        or a top-level shape = the top (nothing lit)."""
        lb = self.listbox
        i = lb.nearest(y)
        box = lb.bbox(i) if i >= 0 else None
        if not box or y >= box[1] + box[3]:
            return None, ""
        r = self.rows[i]
        if r["kind"] == "folder":
            return i, r["folder"]
        f = r["folder"] if r["kind"] == "shape" and r["depth"] else ""  # (a built-in's own file: shown at the top)
        lit = next((k for k, q in enumerate(self.rows) if q["kind"] == "folder" and q["folder"] == f), None)
        return lit, f

    def drag_motion(self, e):
        d = getattr(self, "_drag", None)
        if not d:
            return None
        if d.get("dead"):  # (built-ins only: nothing moves, and never the list's own drag-picking either)
            return "break"
        if not d["on"]:
            if abs(e.y - d["y"]) < self.DRAG_PX and abs(e.x - d["x"]) < self.DRAG_PX:
                return "break"
            d["on"], d["rows"] = True, self.picked_rows()
            self.stop_slow_click()
            if not any(r["path"] or r["kind"] == "folder" for r in d["rows"]):  # (built-ins only: nothing to move)
                d["dead"] = True
                return "break"
            self.listbox.config(cursor="fleur")
        lb = self.listbox
        if e.y < 0:
            lb.yview_scroll(-1, "units")
        elif e.y > lb.winfo_height():
            lb.yview_scroll(1, "units")
        i, folder = self.drop_target(e.y)
        if d["target"] is not None and d["target"] != i and d["target"] < lb.size():
            lb.itemconfig(d["target"], background="")
        if i is not None and i != d["target"]:
            lb.itemconfig(i, background=look.DROP_ROW)
        d["target"], d["folder"] = i, folder
        return "break"  # (never the list's own drag-picking)

    def drag_release(self, e):
        d, self._drag = getattr(self, "_drag", None), None
        if not d:
            return None
        lb = self.listbox
        if d.get("dead"):
            return "break"
        if not d["on"]:
            if d["keep"]:  # (a click on a row picked among several: now only that one)
                lb.selection_clear(0, "end")
                lb.selection_set(d["i"])
                self.sync_library()
            return None
        lb.config(cursor="")
        if d["target"] is not None and d["target"] < lb.size():
            lb.itemconfig(d["target"], background="")
        self.move_rows(d["rows"], d["folder"])
        return "break"

    def drag_cancel(self):
        """A row drag called off (Esc / Delete / right-click while dragging, hunt 2026-11-24): nothing moves, the
        lit row and the pointer go back. True when there was one."""
        d = getattr(self, "_drag", None)
        if not d or not d.get("on") or d.get("dead"):
            return False
        self._drag = {"dead": True}  # (until the let go: the rest of the drag picks nothing either)
        lb = self.listbox
        lb.config(cursor="")
        if d.get("target") is not None and d["target"] < lb.size():
            lb.itemconfig(d["target"], background="")
        return True

    def move_rows(self, rows, into):
        """The rows' shapes and folders moved into the folder `into` ("" = the top). Folders inside a moved folder
        go along; a folder can't go inside itself; built-ins stay. The moved rows are picked after, shown."""
        from window.drawer_library import folder_path, move_folder, move_shape
        folders = [r["folder"] for r in rows if r["kind"] == "folder"]
        folders = [f for f in folders if not any(f.startswith(o + "/") for o in folders)]
        inside = lambda f: any(f == o or f.startswith(o + "/") for o in folders)
        shapes = [r for r in rows if r["kind"] == "shape" and not inside(r["folder"]) and r["folder"] != into]
        new_paths, new_folders, failed = set(), set(), []

        def do(moves):
            for f in folders:
                try:
                    got = move_folder(f, into)
                    moves.append((folder_path(f), folder_path(got)))
                except ValueError:  # (into itself / its own sub folder)
                    failed.append(tr("drawer.move_inside", name=f.rpartition("/")[2]))
                    continue
                except FileExistsError:
                    failed.append(tr("drawer.move_taken", name=f.rpartition("/")[2]))
                    continue
                except OSError as e:
                    failed.append(str(e))
                    continue
                if got != f:
                    self.folder_moved(f, got)
                new_folders.add(got)
            for r in shapes:
                try:
                    to = move_shape(r["path"], into)
                    moves.append((r["path"], to))
                    new_paths.add(to)
                except OSError as e:
                    failed.append(str(e))
        self.keep_fresh(do)
        if failed:
            messagebox.showerror(tr("drawer.spiderweb"), tr("drawer.couldn_t_move", e="\n".join(failed)), parent=self)
        if into:
            self.open_folders.add(into)
        self.pick_folder_rows(new_folders, new_paths)
        self.app.refresh_custom_names()

    def pick_folder_rows(self, folders, paths):
        """Fills the list again with these folders / shape files picked (folders above them opened)."""
        import os
        from window.drawer_library import folder_of
        for f in list(folders) + [folder_of(p) for p in paths]:
            f = parent_of(f) if f in folders else f
            while f:
                self.open_folders.add(f)
                f = parent_of(f)
        self.save_open_folders()
        norm = {os.path.normcase(p) for p in paths}
        self.fill_rows(lambda r: (r["kind"] == "folder" and r["folder"] in folders)
                       or (r["path"] is not None and os.path.normcase(r["path"]) in norm))

    def move_menu(self, m, rows):
        """The list menu's "Move to" submenu: the top + every folder (indented); the picked folders themselves and
        their sub folders greyed, and the one folder all picked rows are already in."""
        sub = tk.Menu(m, tearoff=0)
        picked = [r["folder"] for r in rows if r["kind"] == "folder"]
        here = {r["folder"] if r["kind"] != "folder" else parent_of(r["folder"]) for r in rows}
        same = here.pop() if len(here) == 1 else None
        for f in [""] + library_folders():
            bad = f == same or any(f == p or f.startswith(p + "/") for p in picked)
            label = (INDENT * f.count("/") + f.rpartition("/")[2]) if f else tr("drawer.move_top")
            sub.add_command(label=label, state="disabled" if bad else "normal",
                            command=lambda f=f: self.move_rows(self.picked_rows(), f))
        movable = any(r["path"] or r["kind"] == "folder" for r in rows)
        m.add_cascade(label=tr("drawer.move_to"), menu=sub, state="normal" if movable else "disabled")

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
            i = next((i for i, q in enumerate(self.rows) if q["kind"] == "folder" and q["folder"] == f), None)
            if i is None:
                return
            lb.selection_clear(0, "end")
            lb.selection_set(i)
            lb.activate(i)
            lb.see(i)
            self.sync_library()
