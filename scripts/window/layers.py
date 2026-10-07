"""The drawer's layers list (bottom right, Drawer mixin): one row per stroke, the last drawn on top. The eye column
shows / hides a stroke (hidden ones are left out of the placed shape), the lock column locks it (can't be picked,
moved or erased on the board; still drawn and still stuck to). Rows and board share the picks. Drag rows to change
the order, double-click / F2 renames, Del deletes, Alt+click an eye = only that stroke shown (again = all back).
A stroke's layer data: st["layer"] = {"name", "hidden", "lock", "group"} (custom.clean_layer)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window import look
from window.widgets import Tooltip

SHIFT, CTRL, ALT = 0x1, 0x4, 0x20000
EYE, LOCK = "👁", "🔒"
DRAG_PX = 4  # a press moving further than this drags rows


def kind_key(st):
    """The text key of a stroke's kind, for its default name."""
    if st["kind"] == "poly":
        return "drawer.line" if len(st["pts"]) == 2 else "drawer.polyline"
    return {"ellipse": "drawer.circle", "curve": "drawer.curve", "arc": "drawer.arc"}.get(st["kind"], "drawer.line")


class DrawerLayers:
    # ------------------------------------------------------------ what's shown / pickable

    def layer(self, i):
        return self.strokes[i].get("layer") or {}

    def is_hidden(self, i):
        return bool(self.layer(i).get("hidden"))

    def is_locked(self, i):
        return bool(self.layer(i).get("lock"))

    def pickable(self, i):
        """Stroke i can be clicked, boxed, moved or erased on the board (not hidden, not locked)."""
        return not (self.is_hidden(i) or self.is_locked(i))

    def shown_idx(self):
        return [i for i in range(len(self.strokes)) if not self.is_hidden(i)]

    def shown(self):
        """The strokes the shape is made of (hidden ones left out), without their layer data (so renaming or
        locking doesn't make the areas be worked out again)."""
        return [{k: v for k, v in self.strokes[i].items() if k != "layer"} for i in self.shown_idx()]

    def movable(self):
        """The picked strokes the board can change (moving, flipping, deleting with Del on the board)."""
        return [i for i in self.chosen() if self.pickable(i)]

    def layer_names(self):
        """Each stroke's name in the list: its own, else its kind and a number counting that kind from the
        first drawn ("Curve 2")."""
        counts, out = {}, []
        for st in self.strokes:
            key = kind_key(st)
            counts[key] = counts.get(key, 0) + 1
            out.append((st.get("layer") or {}).get("name") or f"{tr(key)} {counts[key]}")
        return out

    def edit_layers(self, changes):
        """changes = {stroke: {key: value}} (a false value takes the key off). One undo step; strokes hidden go
        out of the picks."""
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
        if not new:
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
        self.changed(settle=False)  # (hiding isn't erasing: coloured areas keep their spots)

    # ------------------------------------------------------------ the list

    def build_layers(self, side):
        s = self.scale
        box = self.layers_box = ttk.LabelFrame(side, text=tr("layers.title"), padding=4)
        box.pack(fill="both", expand=True, pady=(8, 0))
        t = self.layers = ttk.Treeview(box, columns=("eye", "lock"), show="tree headings", selectmode="extended")
        t.heading("#0", text=tr("layers.strokes"), anchor="w")
        t.heading("eye", text=EYE)
        t.heading("lock", text=LOCK)
        t.column("#0", width=int(150 * s), stretch=True)
        for col in ("eye", "lock"):
            t.column(col, width=int(30 * s), minwidth=int(30 * s), stretch=False, anchor="center")
        t.tag_configure("hidden", foreground=look.SOFT_TEXT)
        sb = ttk.Scrollbar(box, orient="vertical", command=t.yview)
        t.config(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        t.bind("<ButtonPress-1>", self.layer_press)
        t.bind("<B1-Motion>", self.layer_drag)
        t.bind("<ButtonRelease-1>", self.layer_release)
        t.bind("<Double-Button-1>", self.layer_double)
        t.bind("<<TreeviewSelect>>", lambda e: self.layer_picked())
        t.bind("<F2>", lambda e: (self.rename_layer(), "break")[1])
        t.bind("<Delete>", lambda e: (self.delete_layers(), "break")[1])
        t.bind("<BackSpace>", lambda e: "break")
        Tooltip(t, tr("layers.tip"))
        self._layer_rows = None   # what the list shows now (sync_layers rebuilds it when this changes)
        self._layer_echo = None   # the rows sync_layers picked (their "picked" event isn't the user's)
        self._layer_press = None  # [row, y at the press, dragged yet, keep the picks]
        self._drop_line = tk.Frame(t, height=max(2, round(2 * s)), background=look.HANDLE)
        self.layer_entry = None   # the box a name is typed into (rename_layer)

    def sync_layers(self):
        """The list as the strokes are now (after every redraw), with the board's picks picked."""
        t = self.layers
        names = self.layer_names()
        rows = tuple((n, self.is_hidden(i), self.is_locked(i)) for i, n in enumerate(names))
        if rows != self._layer_rows:
            self._layer_rows = rows
            t.delete(*t.get_children())
            for i in range(len(rows) - 1, -1, -1):  # (the last drawn on top)
                name, hidden, locked = rows[i]
                t.insert("", "end", iid=f"s{i}", text=name, values=("" if hidden else EYE, LOCK if locked else ""),
                         tags=("hidden",) if hidden else ())
        want = [f"s{i}" for i in self.chosen()]
        if set(t.selection()) != set(want):
            t.selection_set(want)
            if self.sel is not None:
                t.see(f"s{self.sel}")
        self._layer_echo = set(want)  # (the list's "picked" events for this, or for rows rebuilt, come later)

    def row_stroke(self, row):
        return int(row[1:]) if row and row.startswith("s") else None

    def layer_picked(self):
        """Rows picked in the list = strokes picked on the board (the row clicked last = the main one)."""
        t = self.layers
        if set(t.selection()) == self._layer_echo:  # (as the list was set from the board: no click)
            return
        idx = sorted(i for i in map(self.row_stroke, t.selection()) if i is not None)
        if set(idx) == set(self.chosen()):
            return
        focus = self.row_stroke(t.focus())
        main = focus if focus in idx else (idx[-1] if idx else None)
        self.sel, self.picks, self.boxes = main, set(idx) - {main}, []
        self.redraw()

    def layer_press(self, e):
        t = self.layers
        self.end_layer_rename(True)
        if t.identify_region(e.x, e.y) == "heading":
            return "break"
        row = t.identify_row(e.y)
        i = self.row_stroke(row)
        if i is None:  # empty space: nothing picked
            if self.chosen():
                self.deselect()
                self.redraw()
            return "break"
        col = t.identify_column(e.x)
        if col == "#1":  # the eye
            if e.state & ALT:
                self.solo_layer(i)
            else:
                self.edit_layers({i: {"hidden": not self.is_hidden(i)}})
            return "break"
        if col == "#2":  # the lock
            self.edit_layers({i: {"lock": not self.is_locked(i)}})
            return "break"
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
            self.move_layers(self.drop_spot(e.y)[0])
        elif p[3]:  # a click on a picked row: only it picked now
            self.layers.selection_set([p[0]])

    def drop_spot(self, y):
        """Where rows dropped at list height y go: (how many rows above them, the line's y)."""
        t = self.layers
        seen = [(k, t.bbox(r)) for k, r in enumerate(t.get_children())]
        seen = [(k, bx) for k, bx in seen if bx]  # (the rows in view)
        if not seen:
            return 0, 0
        for k, (_, y0, _, h) in seen:
            if y < y0 + h / 2:
                return k, y0
        k, (_, y0, _, h) = seen[-1]
        return k + 1, y0 + h

    def move_layers(self, place):
        """The picked strokes go to row place of the list (counted from the top, rows without them)."""
        n = len(self.strokes)
        top_down = list(range(n - 1, -1, -1))
        moving = set(self.chosen())
        if not moving:
            return
        above = [i for i in top_down[:place] if i not in moving]
        rest = [i for i in top_down if i not in moving]
        order = above + [i for i in top_down if i in moving] + rest[len(above):]
        order.reverse()  # (first drawn first)
        if order == list(range(n)):
            return
        self.push_undo()
        new = {old: k for k, old in enumerate(order)}
        self.strokes = [self.strokes[i] for i in order]
        self.sel = new.get(self.sel)
        self.picks = {new[i] for i in self.picks}
        self.changed(settle=False)

    def solo_layer(self, i):
        """Alt+click an eye: only that stroke shown; when it already is, every stroke shown again."""
        others = [k for k in range(len(self.strokes)) if k != i]
        if not self.is_hidden(i) and others and all(self.is_hidden(k) for k in others):
            self.edit_layers({k: {"hidden": False} for k in others})
        else:
            self.edit_layers({k: {"hidden": k != i} for k in range(len(self.strokes))})

    def delete_layers(self):
        """Del in the list: the picked strokes go (locked or hidden ones too)."""
        idx = self.chosen()
        if idx:
            self.push_undo()
            self.remove_strokes(idx)
            self.changed()

    def layer_double(self, e):
        t = self.layers
        row = t.identify_row(e.y)
        if row and t.identify_column(e.x) == "#0":
            self.rename_layer(row)
        elif row:  # (quick clicks on an eye / lock: each one toggles)
            return self.layer_press(e)
        return "break"

    def rename_layer(self, row=None):
        """A box over the row's name: Enter or a click elsewhere = renamed (empty = its own name back), Esc = not."""
        t = self.layers
        row = row or t.focus() or next(iter(t.selection()), None)
        i = self.row_stroke(row)
        if i is None or self.layer_entry:
            return
        t.see(row)
        t.update_idletasks()
        bx = t.bbox(row, "#0")
        if not bx:
            return
        x, y, w, h = bx
        box = self.layer_entry = ttk.Entry(t)
        box.stroke = i
        box.insert(0, self.layer_names()[i])
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
        i, text = box.stroke, "".join(c for c in box.get() if c >= " ").strip()[:100].strip()
        box.destroy()
        if not keep or i >= len(self.strokes):
            return
        if text == self.layer_names()[i]:  # (unchanged: a name it has by its kind keeps counting along)
            return
        self.edit_layers({i: {"name": text}})
