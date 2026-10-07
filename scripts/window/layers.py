"""The drawer's layers list (bottom right, Drawer mixin): one row per stroke, the last drawn on top. The eye column
shows / hides a stroke (hidden ones are left out of the placed shape), the lock column locks it (can't be picked,
moved or erased on the board; still drawn and still stuck to). Rows and board share the picks. Drag rows to change
the order (into / out of a group too), double-click / F2 renames, Del deletes, Alt+click an eye = only that stroke
shown (again = all back). Groups (right-click > Group / Ungroup): a folder row; a click on the board picks the whole
group, Ctrl+click one stroke. A stroke's layer data: st["layer"] = {"name", "hidden", "lock", "group"}
(custom.clean_layer); a group's strokes always sit together in the list (tidy_groups)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.system import ALT
from window import look

SHIFT, CTRL = 0x1, 0x4
EYE, LOCK = "👁", "🔒"
DRAG_PX = 4  # a press moving further than this drags rows
GROUP = "g:"  # a group row's id: GROUP + its name (a stroke row's: "s" + its number)


def kind_key(st):
    """The text key of a stroke's kind, for its default name."""
    if st["kind"] == "poly":
        return "drawer.line" if len(st["pts"]) == 2 else "drawer.polyline"
    return {"ellipse": "drawer.circle", "curve": "drawer.curve", "arc": "drawer.arc"}.get(st["kind"], "drawer.line")


def row_lines(w):
    """The style "Layers.Treeview": a faint line under each row (user), drawn over the row's own colour (picked rows
    stay blue). The 1 px image is a named Tk image, so it lives as long as the program."""
    st, img = ttk.Style(w), "layers_row_line"
    if img not in w.tk.call("image", "names"):
        w.tk.call("image", "create", "photo", img, "-width", 1, "-height", 1)
        w.tk.call(img, "put", look.LIST_LINE)
    if "Layers.line" not in st.element_names():
        st.element_create("Layers.line", "image", img)
    st.layout("Layers.Treeview.Row", [("Treeitem.row", {"sticky": "nswe", "children": [
        ("Layers.line", {"side": "bottom", "sticky": "we"})]})])


def without_group(st):
    """A copy of the stroke out of its group (pasted copies are strokes of their own)."""
    lay = {k: v for k, v in (st.get("layer") or {}).items() if k != "group"}
    st = {k: v for k, v in st.items() if k != "layer"}
    return dict(st, layer=lay) if lay else st


def pasted(st):
    """A pasted copy of the stroke: out of its group, and a kind-and-number name ("Line 2", kept once the order
    changed) left off, so the copy counts on from the highest number ("Line 4"); other names stay."""
    st = without_group(st)
    lay = st.get("layer") or {}
    word, _, num = (lay.get("name") or "").rpartition(" ")
    if word == tr(kind_key(st)) and num.isdigit():
        lay = {k: v for k, v in lay.items() if k != "name"}
        st = {k: v for k, v in st.items() if k != "layer"}
        return dict(st, layer=lay) if lay else st
    return st


class DrawerLayers:
    # ------------------------------------------------------------ what's shown / pickable

    def layer(self, i):
        return self.strokes[i].get("layer") or {}

    def is_hidden(self, i):
        return bool(self.layer(i).get("hidden"))

    def is_locked(self, i):
        return bool(self.layer(i).get("lock"))

    def group_of(self, i):
        return self.layer(i).get("group")

    def members(self, group):
        return [i for i in range(len(self.strokes)) if self.group_of(i) == group]

    def pickable(self, i):
        """Stroke i can be clicked, boxed, moved or erased on the board (not hidden, not locked)."""
        return not (self.is_hidden(i) or self.is_locked(i))

    def shown_idx(self):
        return [i for i in range(len(self.strokes)) if not self.is_hidden(i)]

    def shown(self):
        """The strokes the shape is made of (hidden ones left out), without their layer data (so renaming or
        locking doesn't make the areas be worked out again)."""
        return [{k: v for k, v in self.strokes[i].items() if k != "layer"} for i in self.shown_idx()]

    def bare(self):
        """Every stroke, hidden ones too, without its layer data."""
        return [{k: v for k, v in st.items() if k != "layer"} for st in self.strokes]

    def movable(self):
        """The picked strokes the board can change (moving, flipping, deleting with Del on the board)."""
        return [i for i in self.chosen() if self.pickable(i)]

    def mates(self, i):
        """What a click on stroke i picks: its whole group (the strokes of it the board can pick), else i."""
        g = self.group_of(i)
        return [k for k in self.members(g) if self.pickable(k)] if g else [i]

    def layer_names(self):
        """Each stroke's name in the list: its own, else its kind and the next number of that kind from the first
        drawn ("Curve 2"; names kept as "Curve 5" count too, so no two get the same)."""
        last, out = {}, []
        for st in self.strokes:
            key = kind_key(st)
            name = (st.get("layer") or {}).get("name")
            word, _, num = (name or "").rpartition(" ")
            if name and word == tr(key) and num.isdigit():
                last[key] = max(last.get(key, 0), int(num))
            elif not name:
                last[key] = last.get(key, 0) + 1
                name = f"{tr(key)} {last[key]}"
            out.append(name)
        return out

    def edit_layers(self, changes, order=None):
        """changes = {stroke: {key: value}} (a false value takes the key off); order = the strokes' new order
        (first drawn first), if it changes too. One undo step; strokes hidden go out of the picks; a group's
        strokes are put back together."""
        new = {}
        for i, ch in changes.items():
            lay = dict(self.layer(i))
            for k, v in ch.items():
                if v:
                    lay[k] = v
                else:
                    lay.pop(k, None)
            if lay != self.layer(i):
                new[i] = lay
        if not new and (order is None or order == list(range(len(self.strokes)))):
            return
        self.push_undo()
        for i, lay in new.items():
            st = dict(self.strokes[i])
            st.pop("layer", None)
            if lay:
                st["layer"] = lay
            self.strokes[i] = st
        gone = {i for i in new if self.is_hidden(i)}
        if self.sel in gone:
            self.sel = None
        self.picks -= gone
        if order is not None:
            self.reorder(order)
        self.tidy_groups()
        self.changed(settle=False)  # (hiding isn't erasing: coloured areas keep their spots)

    def keep_names(self):
        """Before strokes change places or go: each one keeps the name it has now ("Line 3" stays "Line 3")."""
        for i, name in enumerate(self.layer_names()):
            if not self.layer(i).get("name"):
                self.strokes[i] = dict(self.strokes[i], layer=dict(self.layer(i), name=name))

    def reorder(self, order):
        """The strokes in this order (old numbers, first drawn first); the picks stay on the same strokes."""
        if order != list(range(len(self.strokes))):
            self.keep_names()
        new = {old: k for k, old in enumerate(order)}
        self.strokes = [self.strokes[i] for i in order]
        self.sel = new.get(self.sel)
        self.picks = {new[i] for i in self.picks}

    def tidy_groups(self):
        """Every group's strokes together, where its top one is in the list."""
        top_down, done, out = list(range(len(self.strokes) - 1, -1, -1)), set(), []
        for i in top_down:
            if i not in done:
                g = self.group_of(i)
                run = [k for k in top_down if self.group_of(k) == g] if g else [i]
                out += run
                done.update(run)
        if out[::-1] != list(range(len(self.strokes))):
            self.reorder(out[::-1])

    def group_strokes(self, idx):
        """The strokes idx become a new group ("Group 3"), together where the top one is."""
        if not idx:
            return
        taken, n = {self.group_of(i) for i in range(len(self.strokes))}, 1
        while tr("layers.group_name", n=n) in taken:
            n += 1
        self.edit_layers({i: {"group": tr("layers.group_name", n=n)} for i in idx})

    def ungroup(self, idx):
        """The groups of the strokes idx are taken apart (their strokes stay where they are)."""
        groups = {self.group_of(i) for i in idx} - {None}
        self.edit_layers({i: {"group": None} for g in groups for i in self.members(g)})

    def layer_menu_items(self, m, idx):
        """Group / Ungroup, Invert selection, Hide / Lock selected in a stroke menu (the board's and the list's)."""
        m.add_command(label=tr("layers.group"), command=lambda: self.group_strokes(idx),
                      state="normal" if idx else "disabled")
        m.add_command(label=tr("layers.ungroup"), command=lambda: self.ungroup(idx),
                      state="normal" if any(self.group_of(i) for i in idx) else "disabled")
        m.add_separator()
        m.add_command(label=tr("layers.invert"), command=self.invert_picks)
        hidden = bool(idx) and all(self.is_hidden(i) for i in idx)  # (all already: the other way)
        m.add_command(label=tr("layers.show_picked" if hidden else "layers.hide_picked"),
                      command=lambda: self.edit_layers({i: {"hidden": not hidden} for i in idx}))
        locked = bool(idx) and all(self.is_locked(i) for i in idx)
        m.add_command(label=tr("layers.unlock_picked" if locked else "layers.lock_picked"),
                      command=lambda: self.edit_layers({i: {"lock": not locked} for i in idx}))

    def invert_picks(self):
        """Invert selection: every shown stroke not picked is picked instead (locked ones too, as in the list)."""
        now = set(self.chosen())
        idx = [i for i in self.shown_idx() if i not in now]
        self.sel, self.picks, self.boxes = (idx[-1] if idx else None), set(idx[:-1]), []
        self.redraw()

    # ------------------------------------------------------------ the list

    def build_layers(self, side):
        s = self.scale
        box = self.layers_box = ttk.LabelFrame(side, text=tr("layers.title"), padding=4)
        box.pack(fill="both", expand=True, pady=(8, 0))
        row_lines(self)
        t = self.layers = ttk.Treeview(box, style="Layers.Treeview", columns=("eye", "lock"), show="tree headings",
                                       selectmode="extended", height=8)  # (its least rows: past that the panel scrolls)
        t.heading("#0", text=tr("layers.strokes"), anchor="w")
        t.heading("eye", text=EYE)
        t.heading("lock", text=LOCK)
        t.column("#0", width=int(150 * s), stretch=True)
        for col in ("eye", "lock"):
            t.column(col, width=int(30 * s), minwidth=int(30 * s), stretch=False, anchor="center")
        t.tag_configure("hidden", foreground=look.SOFT_TEXT)
        t.tag_configure("group", font=look.font(9, "bold"))
        sb = ttk.Scrollbar(box, orient="vertical", command=t.yview)
        t.config(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        t.bind("<ButtonPress-1>", self.layer_press)
        t.bind("<B1-Motion>", self.layer_drag)
        t.bind("<ButtonRelease-1>", self.layer_release)
        t.bind("<Double-Button-1>", self.layer_double)
        t.bind("<ButtonPress-3>", self.layer_right_click)
        t.bind("<<TreeviewSelect>>", lambda e: self.layer_picked())
        t.bind("<F2>", lambda e: (self.rename_layer(), "break")[1])
        t.bind("<Delete>", lambda e: (self.delete_layers(), "break")[1])
        t.bind("<BackSpace>", lambda e: "break")
        self._layer_rows = None   # what the list shows now (sync_layers rebuilds it when this changes)
        self._layer_echo = None   # the rows sync_layers picked (their "picked" event isn't the user's)
        self._layer_press = None  # [row, y at the press, dragged yet, keep the picks]
        self._closed = set()      # groups whose rows are folded away
        self._drop_line = tk.Frame(t, height=max(2, round(2 * s)), background=look.HANDLE)
        self.layer_entry = None   # the box a name is typed into (rename_layer)

    def sync_layers(self):
        """The list as the strokes are now (after every redraw), with the board's picks picked."""
        t = self.layers
        names = self.layer_names()
        rows = tuple((n, self.is_hidden(i), self.is_locked(i), self.group_of(i)) for i, n in enumerate(names))
        for g in t.get_children():  # (folded groups stay folded)
            if g.startswith(GROUP):
                (self._closed.discard if t.item(g, "open") else self._closed.add)(g[len(GROUP):])
        if rows != self._layer_rows:
            self._layer_rows = rows
            t.delete(*t.get_children())
            for i in range(len(rows) - 1, -1, -1):  # (the last drawn on top)
                name, hidden, locked, g = rows[i]
                parent = ""
                if g:
                    parent = GROUP + g
                    if not t.exists(parent):
                        mem = self.members(g)
                        off = all(self.is_hidden(k) for k in mem)
                        t.insert("", "end", iid=parent, text=g, open=g not in self._closed,
                                 values=("" if off else EYE, LOCK if all(self.is_locked(k) for k in mem) else ""),
                                 tags=("group", "hidden") if off else ("group",))
                t.insert(parent, "end", iid=f"s{i}", text=name,
                         values=("" if hidden else EYE, LOCK if locked else ""), tags=("hidden",) if hidden else ())
        chosen = set(self.chosen())
        want = [f"s{i}" for i in chosen] + [GROUP + g for g in {self.group_of(i) for i in chosen} - {None}
                                            if set(self.members(g)) <= chosen]
        if set(t.selection()) != set(want):
            t.selection_set(want)
            if self.sel is not None:
                t.see(f"s{self.sel}")
        self._layer_echo = set(want)  # (the list's "picked" events for this, or for rows rebuilt, come later)

    def row_stroke(self, row):
        return int(row[1:]) if row and row.startswith("s") else None

    def row_members(self, row):
        """The strokes a row stands for: its stroke, or its group's."""
        if row and row.startswith(GROUP):
            return self.members(row[len(GROUP):])
        i = self.row_stroke(row)
        return [] if i is None else [i]

    def layer_picked(self):
        """Rows picked in the list = strokes picked on the board (the row clicked last = the main one)."""
        t = self.layers
        if set(t.selection()) == self._layer_echo:  # (as the list was set from the board: no click)
            return
        idx = sorted({i for row in t.selection() for i in self.row_members(row)})
        if set(idx) == set(self.chosen()):
            return
        focus = self.row_members(t.focus())
        main = focus[-1] if focus and focus[-1] in idx else (idx[-1] if idx else None)
        self.sel, self.picks, self.boxes = main, set(idx) - {main}, []
        self.redraw()

    def layer_press(self, e):
        t = self.layers
        self.end_layer_rename(True)
        if t.identify_region(e.x, e.y) == "heading":
            return "break"
        row = t.identify_row(e.y)
        idx = self.row_members(row)
        if not idx:  # empty space: nothing picked
            if self.chosen():
                self.deselect()
                self.redraw()
            return "break"
        col = t.identify_column(e.x)
        if col == "#1":  # the eye (a group's: all its strokes)
            if e.state & ALT:
                self.solo_layer(idx)
            else:
                off = not all(self.is_hidden(i) for i in idx)
                self.edit_layers({i: {"hidden": off} for i in idx})
            return "break"
        if col == "#2":  # the lock
            on = not all(self.is_locked(i) for i in idx)
            self.edit_layers({i: {"lock": on} for i in idx})
            return "break"
        if "indicator" in t.identify_element(e.x, e.y):  # a group's fold arrow
            return None
        keep = row in t.selection() and not e.state & (CTRL | SHIFT)
        self._layer_press = [row, e.y, False, keep]
        if keep:  # (a picked row pressed: the picks stay, so they can be dragged together)
            t.focus(row)
            return "break"
        return None

    def layer_drag(self, e):
        p = self._layer_press
        if not p:
            return "break"
        if not p[2] and abs(e.y - p[1]) > DRAG_PX:
            p[2] = True
        if p[2]:
            _, y = self.drop_spot(e.y)
            self._drop_line.place(x=0, y=y - 1, relwidth=1)
        return "break"

    def layer_release(self, e):
        p, self._layer_press = self._layer_press, None
        self._drop_line.place_forget()
        if not p:
            return
        if p[2]:
            self.move_layers(self.drop_spot(e.y)[0], whole=p[0].startswith(GROUP))
        elif p[3]:  # a click on a picked row: only it picked now
            self.layers.selection_set([p[0]])

    def visible_rows(self):
        """The rows top to bottom as shown (a folded group's strokes left out)."""
        t, out = self.layers, []
        for r in t.get_children():
            out.append(r)
            if r.startswith(GROUP) and t.item(r, "open"):
                out += t.get_children(r)
        return out

    def drop_spot(self, y):
        """Where rows dropped at list height y go: (how many shown rows above them, the line's y)."""
        t = self.layers
        seen = [(k, t.bbox(r)) for k, r in enumerate(self.visible_rows())]
        seen = [(k, bx) for k, bx in seen if bx]  # (the rows in view)
        if not seen:
            return 0, 0
        for k, (_, y0, _, h) in seen:
            if y < y0 + h / 2:
                return k, y0
        k, (_, y0, _, h) = seen[-1]
        return k + 1, y0 + h

    def move_layers(self, place, whole=False):
        """The picked strokes go below the first `place` shown rows. Dropped between two strokes of a group (or
        right under its row) they join it, elsewhere they leave their group, unless whole groups are moved (a
        group row dragged, or the picks are whole groups): those stay groups and go above a group they land in."""
        moving = set(self.chosen())
        if not moving:
            return
        rows = self.visible_rows()
        top_down = list(range(len(self.strokes) - 1, -1, -1))
        groups = {self.group_of(i) for i in moving}
        whole = whole or (None not in groups and moving == {i for g in groups for i in self.members(g)})

        def group_at(row):  # the group a drop beside this row lands in
            if row is None:
                return None
            if row.startswith(GROUP):
                return row[len(GROUP):] if self.layers.item(row, "open") else None
            return self.group_of(self.row_stroke(row))

        def through(row):  # how many strokes (top down) are at or above this row
            mem = self.row_members(row)
            if row.startswith(GROUP) and self.layers.item(row, "open"):
                return min(top_down.index(i) for i in mem)  # (the group's row is above its strokes)
            return max(top_down.index(i) for i in mem) + 1

        above_row = rows[place - 1] if place else None
        below_row = rows[place] if place < len(rows) else None
        target = group_at(above_row)
        if above_row and not above_row.startswith(GROUP) and group_at(below_row) != target:
            target = None  # (below a group's last stroke: out of it)
        cut = through(above_row) if above_row else 0
        if whole and target:  # (no group in a group: above the one landed in)
            cut, target = min(top_down.index(i) for i in self.members(target)), None
        above = [i for i in top_down[:cut] if i not in moving]
        rest = [i for i in top_down if i not in moving]
        order = above + [i for i in top_down if i in moving] + rest[len(above):]
        changes = {} if whole else {i: {"group": target} for i in moving}
        self.edit_layers(changes, order=order[::-1])

    def solo_layer(self, idx):
        """Alt+click an eye: only those strokes shown; when they already are, every stroke shown again."""
        others = [k for k in range(len(self.strokes)) if k not in idx]
        if not any(self.is_hidden(i) for i in idx) and others and all(self.is_hidden(k) for k in others):
            self.edit_layers({k: {"hidden": False} for k in others})
        else:
            self.edit_layers({k: {"hidden": k not in idx} for k in range(len(self.strokes))})

    def delete_layers(self):
        """Del in the list: the picked strokes go (locked or hidden ones too)."""
        idx = self.chosen()
        if idx:
            self.push_undo()
            self.remove_strokes(idx)
            self.changed()

    def layer_right_click(self, e):
        """A row's menu: Group / Ungroup / Rename / Delete (the row picked first if it isn't)."""
        t = self.layers
        self.end_layer_rename(True)
        row = t.identify_row(e.y)
        if not row:
            return "break"
        if row not in t.selection():
            t.selection_set([row])
            t.focus(row)
            self.layer_picked()
        idx = self.chosen()
        m = tk.Menu(self, tearoff=0)
        self.layer_menu_items(m, idx)
        m.add_separator()
        m.add_command(label=tr("layers.rename"), accelerator="F2", command=lambda: self.rename_layer(row))
        m.add_command(label=tr("layers.delete"), accelerator=tr("drawer.del"), command=self.delete_layers)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
        return "break"

    def layer_double(self, e):
        t = self.layers
        row = t.identify_row(e.y)
        if row and t.identify_column(e.x) == "#0":
            if "indicator" in t.identify_element(e.x, e.y):
                return None
            self.rename_layer(row)
        elif row:  # (quick clicks on an eye / lock: each one toggles)
            return self.layer_press(e)
        return "break"

    def rename_layer(self, row=None):
        """A box over the row's name: Enter or a click elsewhere = renamed (a stroke's empty name = its own name
        back), Esc = not."""
        t = self.layers
        row = row or t.focus() or next(iter(t.selection()), None)
        if not self.row_members(row) or self.layer_entry:
            return
        t.see(row)
        t.update_idletasks()
        bx = t.bbox(row, "#0")
        if not bx:
            return
        x, y, w, h = bx
        box = self.layer_entry = ttk.Entry(t)
        box.row = row
        box.insert(0, t.item(row, "text"))
        box.select_range(0, "end")
        box.icursor("end")
        box.place(x=x, y=y - 2, width=w, height=h + 4)
        box.focus_set()
        box.bind("<Return>", lambda e: (self.end_layer_rename(True), t.focus_set(), "break")[2])
        box.bind("<Escape>", lambda e: (self.end_layer_rename(False), t.focus_set(), "break")[2])
        box.bind("<FocusOut>", lambda e: self.end_layer_rename(True))

    def end_layer_rename(self, keep):
        box, self.layer_entry = self.layer_entry, None
        if not box:
            return
        row, text = box.row, "".join(c for c in box.get() if c >= " ").strip()[:100].strip()
        box.destroy()
        if not keep:
            return
        if row.startswith(GROUP):  # a group: all its strokes (a name another group has gets a number)
            old = row[len(GROUP):]
            if not text or text == old or not self.members(old):
                return
            taken, name, k = {self.group_of(i) for i in range(len(self.strokes))} - {old}, text, 2
            while name in taken:
                name, k = f"{text} ({k})", k + 1
            self.edit_layers({i: {"group": name} for i in self.members(old)})
            return
        i = self.row_stroke(row)
        if i >= len(self.strokes) or text == self.layer_names()[i]:  # (unchanged: a name it has by its kind
            return  # keeps counting along)
        self.edit_layers({i: {"name": text}})
