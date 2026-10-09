"""The Hz bass window's layers strip (left of its piano roll; the toolbar's Layers button shows / hides it, off to
start with): one row per layer of the Hz bass shown (hzbass.clean_layers), like a DAW's tracks. Click a row = the
layer edited (its notes and sound shown; the others' notes faded), M / S = mute / solo, the colour square = its
colour, double-click the name = rename, right-click = menu, Delete = the picked layer goes, + = a new layer."""

import copy
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.hzbass import LAYER_NAME, LAYERS, SOUND, fit_length, layers_of, with_layers
from roll.roll_shared import SLOT_COLORS
from window import look
from window.widgets import Tooltip


def plain_layers(hz):
    """hz with its layers list made even when it has one layer only (that one then gets a name and a colour)."""
    return hz if hz.get("layers") else dict(hz, layers=[{"name": "", "colour": 0}], layer=0)


def layer_colour(hz, i):
    """(fill, edge) of a layer's notes."""
    got = (hz.get("layers") or [{}])[i] if i < len(hz.get("layers") or [{}]) else {}
    return SLOT_COLORS[got.get("colour", 0) % len(SLOT_COLORS)]


class LayerStrip:
    def __init__(self, win):
        self.win, self.app, s = win, win.app, win.s
        self.s = s
        self.row_h = round(24 * s)
        self.box = ttk.Frame(win, width=round(170 * s))
        self.box.pack_propagate(False)
        head = ttk.Frame(self.box, padding=(6, 2, 4, 2))
        head.pack(fill="x")
        ttk.Label(head, text=tr("hz.layers")).pack(side="left")
        self.add_btn = ttk.Button(head, text="+", width=3, command=self.add, takefocus=False)
        self.add_btn.pack(side="right")
        Tooltip(self.add_btn, tr("hz.layer_add_tip"))
        c = self.canvas = tk.Canvas(self.box, background=look.HZ_LAYER_BG, highlightthickness=0, takefocus=True)
        c.pack(fill="both", expand=True)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Delete>", lambda e: self.remove(self.picked()))
        self.naming = None  # (layer number, Entry) while a name is typed
        self.palette = None  # the colour squares' popup

    # ------------------------------------------------------------ what it shows

    def hz(self):
        """The Hz bass shown in the window, or None (no Hz bass yet: nothing to put layers in)."""
        sh = self.win.target()
        return (sh or {}).get("hz") or None

    def picked(self):
        hz = self.hz()
        return hz.get("layer", 0) if hz else 0

    def name_of(self, hz, i):
        e = plain_layers(hz)["layers"][i]
        return e.get("name") or tr("hz.layer_n", n=i + 1)

    def show(self, on):
        if on:
            self.box.pack(side="left", fill="y", before=self.win.notes_box)
            self.redraw()
        else:
            self.end_naming(False)
            self.box.pack_forget()

    def spots(self, i):
        """x spans of a row's parts: colour square, M, S."""
        w, s = self.canvas.winfo_width(), self.s
        b = round(18 * s)
        return (round(6 * s), round(6 * s) + round(12 * s)), (w - 2 * b - round(8 * s), w - b - round(8 * s)), \
            (w - b - round(4 * s), w - round(4 * s))

    def redraw(self):
        c = self.canvas
        if not self.box.winfo_manager():
            return
        c.delete("all")
        hz = self.hz()
        self.add_btn.config(state="normal" if hz is not None and len(plain_layers(hz)["layers"]) < LAYERS
                            else "disabled")
        w = c.winfo_width()
        if hz is None:
            c.create_text(w / 2, 20 * self.s, text=tr("hz.layers_none"), fill=look.HINT, width=w - 12 * self.s,
                          justify="center")
            return
        info, picked, rh, s = plain_layers(hz)["layers"], hz.get("layer", 0), self.row_h, self.s
        solo = any(e.get("solo") for e in info)
        for i, e in enumerate(info):
            y = i * rh
            if i == picked:
                c.create_rectangle(0, y, w, y + rh, fill=look.HZ_LAYER_PICKED, outline="")
            c.create_line(0, y + rh - 1, w, y + rh - 1, fill=look.LIST_LINE)
            (a0, a1), (m0, m1), (s0, s1) = self.spots(i)
            fill, edge = SLOT_COLORS[e.get("colour", 0) % len(SLOT_COLORS)]
            c.create_rectangle(a0, y + rh / 2 - 6 * s, a1, y + rh / 2 + 6 * s, fill=fill, outline=edge)
            heard = e.get("solo") if solo else not e.get("mute")
            c.create_text(a1 + 6 * s, y + rh / 2, text=self.name_of(hz, i), anchor="w", font=look.font(9),
                          fill=look.LABEL if heard else look.HINT, width=max(10, m0 - a1 - 10 * s))
            for x0, x1, key, colour in ((m0, m1, "mute", look.HZ_LAYER_MUTE), (s0, s1, "solo", look.HZ_LAYER_SOLO)):
                c.create_rectangle(x0, y + 4 * s, x1, y + rh - 4 * s, fill=colour if e.get(key) else look.HZ_LAYER_OFF,
                                   outline=look.HZ_EDGE)
                c.create_text((x0 + x1) / 2, y + rh / 2, text=tr("hz.layer_" + key + "_key"), font=look.font(8, "bold"),
                              fill=look.HZ_LAYER_ON_TEXT if e.get(key) else look.LABEL)

    def row_at(self, y):
        hz = self.hz()
        if hz is None:
            return None
        i = int(y // self.row_h)
        return i if 0 <= i < len(plain_layers(hz)["layers"]) else None

    # ------------------------------------------------------------ mouse

    def on_press(self, e):
        self.canvas.focus_set()
        self.end_naming(True)
        i = self.row_at(e.y)
        if i is None or self.win.drag:
            return
        (a0, a1), (m0, m1), (s0, s1) = self.spots(i)
        if a0 - 3 <= e.x <= a1 + 3:
            self.pick(i)
            self.ask_colour(i)
        elif m0 <= e.x <= m1:
            self.flag(i, "mute")
        elif s0 <= e.x <= s1:
            self.flag(i, "solo")
        else:
            self.pick(i)

    def on_double(self, e):
        i = self.row_at(e.y)
        if i is not None and self.spots(i)[0][1] + 3 < e.x < self.spots(i)[1][0]:
            self.start_naming(i)

    def on_menu(self, e):
        i = self.row_at(e.y)
        if i is None or self.win.drag:
            return
        self.pick(i)
        hz = self.hz()
        m = tk.Menu(self.canvas, tearoff=False)
        m.add_command(label=tr("hz.layer_rename"), command=lambda: self.start_naming(i))
        m.add_command(label=tr("hz.layer_colour"), command=lambda: self.ask_colour(i))
        m.add_separator()
        m.add_command(label=tr("hz.layer_add"), command=self.add,
                      state="normal" if len(plain_layers(hz)["layers"]) < LAYERS else "disabled")
        m.add_command(label=tr("hz.layer_delete"), command=lambda: self.remove(i),
                      state="normal" if len(plain_layers(hz)["layers"]) > 1 else "disabled")
        m.tk_popup(e.x_root, e.y_root)

    # ------------------------------------------------------------ changes

    def put(self, hz, step):
        """hz becomes the Hz bass shown: one undo step named step (None: no step, e.g. picking a layer, as a DAW's
        track picked isn't one)."""
        win, app = self.win, self.app
        sh = win.target()
        if sh is None:
            return
        if step:
            win.drop_drag()
            win.own_step = True
            try:
                app.push_undo(name=step)
            finally:
                win.own_step = False
        sh["hz"] = hz
        if hz.get("grow"):
            fit_length(sh)
        app.shapes_changed()
        app.sync_custom()
        app.schedule_autosave()
        win.sync()

    def pick(self, i):
        hz = self.hz()
        if hz is None or i == hz.get("layer", 0) or not hz.get("layers"):
            return
        self.put(with_layers(hz, layers_of(hz), i), None)

    def add(self):
        """A new empty layer under the others, picked (its sound as a new Hz bass's)."""
        hz = self.hz()
        if hz is None:
            return
        hz = plain_layers(copy.deepcopy(hz))
        if len(hz["layers"]) >= LAYERS:
            return self.win.bell()
        used = {e.get("colour", 0) % len(SLOT_COLORS) for e in hz["layers"]}
        colour = next((k for k in range(len(SLOT_COLORS)) if k not in used), len(hz["layers"]) % len(SLOT_COLORS))
        every = layers_of(hz)
        new = {k: v for k, v in every[0].items() if k not in SOUND}
        hz = dict(hz, layers=hz["layers"] + [{"name": "", "colour": colour}])
        self.put(with_layers(hz, every + [new], len(every)), tr("hz.step_layer_add"))

    def remove(self, i):
        """Layer i goes, with its notes (the last one stays)."""
        hz = self.hz()
        if hz is None or len(hz.get("layers") or ()) < 2:
            return self.win.bell()
        every, info = layers_of(hz), list(hz["layers"])
        picked = hz.get("layer", 0)
        del every[i], info[i]
        picked = min(picked if picked < i else max(0, picked - 1) if picked > i else i, len(info) - 1)
        self.put(with_layers(dict(hz, layers=info), every, picked), tr("hz.step_layer_delete"))

    def change(self, i, step, **what):
        """Layer i's name / colour / mute / solo set (None = taken out)."""
        hz = self.hz()
        if hz is None:
            return
        hz = plain_layers(copy.deepcopy(hz))
        e = hz["layers"][i]
        for k, v in what.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        self.put(hz, step)

    def flag(self, i, key):
        hz = self.hz()
        on = not plain_layers(hz)["layers"][i].get(key)
        self.change(i, tr("hz.step_layer_" + key), **{key: True if on else None})

    # ------------------------------------------------------------ typing a name

    def start_naming(self, i):
        self.end_naming(True)
        hz = self.hz()
        if hz is None:
            return
        c, rh = self.canvas, self.row_h
        (a0, a1), (m0, m1), _ = self.spots(i)
        var = tk.StringVar(value=plain_layers(hz)["layers"][i].get("name") or self.name_of(hz, i))
        entry = ttk.Entry(c, textvariable=var)
        c.create_window(a1 + 4 * self.s, i * rh + rh / 2, window=entry, anchor="w", width=m0 - a1 - 8 * self.s,
                        tags="naming")
        entry.select_range(0, "end")
        entry.focus_set()
        self.naming = (i, entry, var)
        entry.bind("<Return>", lambda e: self.end_naming(True) or "break")
        entry.bind("<Escape>", lambda e: self.end_naming(False) or "break")
        entry.bind("<FocusOut>", lambda e: self.end_naming(True))

    def end_naming(self, keep):
        if self.naming is None:
            return
        i, entry, var = self.naming
        self.naming = None
        name = var.get().strip()[:LAYER_NAME]
        self.canvas.delete("naming")
        entry.destroy()
        hz = self.hz()
        if keep and hz is not None and i < len(plain_layers(hz)["layers"]) and name != self.name_of(hz, i):
            self.change(i, tr("hz.step_layer_rename"), name=name or None)
        else:
            self.redraw()
        self.canvas.focus_set()

    # ------------------------------------------------------------ colour

    def ask_colour(self, i):
        """A small popup of the piano roll's note colours under the row; a click picks one."""
        if self.palette is not None:
            self.palette.destroy()
        c, s = self.canvas, self.s
        top = self.palette = tk.Toplevel(c)
        top.overrideredirect(True)
        top.transient(self.win)
        size, cols = round(20 * s), 5
        pad = round(3 * s)
        pal = tk.Canvas(top, width=cols * size + 2 * pad, height=-(-len(SLOT_COLORS) // cols) * size + 2 * pad,
                        background=look.HZ_LAYER_BG, highlightthickness=1, highlightbackground=look.HZ_EDGE)
        pal.pack()
        for k, (fill, edge) in enumerate(SLOT_COLORS):
            x, y = pad + k % cols * size, pad + k // cols * size
            pal.create_rectangle(x + 2, y + 2, x + size - 2, y + size - 2, fill=fill, outline=edge)

        def picked(e):
            k = int((e.y - pad) // size) * cols + int((e.x - pad) // size)
            close()
            if 0 <= k < len(SLOT_COLORS) and 0 <= e.x - pad < cols * size:
                self.change(i, tr("hz.step_layer_colour"), colour=k)

        def close(e=None):
            if self.palette is top:
                self.palette = None
            top.destroy()
        pal.bind("<ButtonRelease-1>", picked)
        top.bind("<Escape>", close)
        top.bind("<FocusOut>", close)
        top.geometry(f"+{c.winfo_rootx() + round(4 * s)}+{c.winfo_rooty() + (i + 1) * self.row_h}")
        top.focus_force()
