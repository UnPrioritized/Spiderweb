"""The Hz bass window's layers strip (left of its piano roll; the toolbar's Layers button shows / hides it, off to
start with): one row per layer of the Hz bass shown (hzbass.clean_layers), like a DAW's tracks. Click a row = the
layer edited (its notes and sound shown; the others' notes faded), M / S = mute / solo, the colour square = its
colour, double-click the name (or click the picked one's name again, slowly) = rename, drag a row up / down = its
place in the list, right-click = menu, Delete = the picked layer goes, + = a new layer."""

import copy
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from files.lang import tr
from files.system import double_click_ms
from notes.hzbass import LAYER_NAME, LAYERS, SOUND, all_tones, fit_length, layers_of, left_edge, with_layers
from roll.roll_shared import SLOT_COLORS
from window import look
from window.hz_gates import hz_keys, hz_made
from window.widgets import Tooltip

DOUBLE_MS = double_click_ms()  # (the system's own setting)


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
        # a line between the list and the piano roll's keys
        tk.Frame(self.box, width=round(3 * s), background=look.HZ_LAYER_SEP).pack(side="right", fill="y")
        head = ttk.Frame(self.box, padding=(6, 2, 4, 2))
        head.pack(fill="x")
        ttk.Label(head, text=tr("hz.layers")).pack(side="left")
        self.add_btn = ttk.Button(head, text="+", width=3, command=self.add, takefocus=False)
        self.add_btn.pack(side="right")
        Tooltip(self.add_btn, tr("hz.layer_add_tip"))
        c = self.canvas = tk.Canvas(self.box, background=look.HZ_LAYER_BG, highlightthickness=0, takefocus=True,
                                    yscrollincrement=self.row_h)
        self.bar = ttk.Scrollbar(self.box, orient="vertical", command=c.yview)  # (shown only when rows don't fit)
        c.config(yscrollcommand=self.bar.set)
        c.pack(fill="both", expand=True)
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<MouseWheel>", lambda e: c.yview_scroll(-1 if e.delta > 0 else 1, "units") if self.bar.winfo_manager()
               else None)
        self.name_font = tkfont.Font(font=look.font(9))
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Delete>", lambda e: (self.drop_held(), self.remove(self.picked())))  # (a row held: let go first)
        c.bind("<Escape>", lambda e: self.drop_held())
        self.naming = None  # (layer number, Entry, its text, the shape) while a name is typed
        self.palette = None  # the colour squares' popup
        self.held = None  # (row, press y, rename on let go, moved) while a row's name is held
        self.drop = None  # where a dragged row would go (0 = above the first row ... n = under the last)
        self.slow = None  # the slow second click's timer

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
            self.settle()
            self.box.pack_forget()

    def settle(self):
        """Undo / redo coming, or the list hidden: a name being typed, the colour popup and a held row are called
        off (they point at rows by number)."""
        self.end_naming(False)
        self.close_palette()
        self.drop_held()

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
        c.delete(*[k for k in c.find_all() if "naming" not in c.gettags(k)])  # (a name being typed stays)
        hz = self.hz()
        self.add_btn.config(state="normal" if hz is not None and len(plain_layers(hz)["layers"]) < LAYERS
                            else "disabled")
        w = c.winfo_width()
        if hz is None:
            self.show_bar(False)
            c.create_text(w / 2, 20 * self.s, text=tr("hz.layers_none"), fill=look.HINT, width=w - 12 * self.s,
                          justify="center")
            return
        info, picked, rh, s = plain_layers(hz)["layers"], hz.get("layer", 0), self.row_h, self.s
        self.show_bar(len(info) * rh > c.winfo_height())
        c.config(scrollregion=(0, 0, w, max(len(info) * rh, c.winfo_height())))
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
            c.create_text(a1 + 6 * s, y + rh / 2, text=self.cut_name(self.name_of(hz, i), m0 - a1 - 10 * s),
                          anchor="w", font=self.name_font, fill=look.LABEL if heard else look.HINT)
            for x0, x1, key, colour in ((m0, m1, "mute", look.HZ_LAYER_MUTE), (s0, s1, "solo", look.HZ_LAYER_SOLO)):
                c.create_rectangle(x0, y + 4 * s, x1, y + rh - 4 * s, fill=colour if e.get(key) else look.HZ_LAYER_OFF,
                                   outline=look.HZ_EDGE)
                c.create_text((x0 + x1) / 2, y + rh / 2, text=tr("hz.layer_" + key + "_key"), font=look.font(8, "bold"),
                              fill=look.HZ_LAYER_ON_TEXT if e.get(key) else look.LABEL)
        if self.drop is not None and self.held and self.drop not in (self.held[0], self.held[0] + 1):
            y = min(self.drop * rh, len(info) * rh - 1)
            c.create_line(0, y, w, y, fill=look.HZ_LAYER_DROP, width=max(2, round(2 * s)))
        c.tag_raise("naming")

    def show_bar(self, on):
        if on != bool(self.bar.winfo_manager()):
            if on:
                self.bar.pack(side="right", fill="y", before=self.canvas)
            else:
                self.bar.pack_forget()
                self.canvas.yview_moveto(0)

    def cut_name(self, name, room):
        """name on one line: cut with "…" to fit room pixels."""
        f = self.name_font
        if f.measure(name) <= room:
            return name
        while name and f.measure(name + "…") > room:
            name = name[:-1]
        return name.rstrip() + "…"

    def row_at(self, y):
        """The row at the mouse's y in the list (scrolled or not), or None."""
        hz = self.hz()
        if hz is None:
            return None
        i = int(self.canvas.canvasy(y) // self.row_h)
        return i if 0 <= i < len(plain_layers(hz)["layers"]) else None

    # ------------------------------------------------------------ mouse

    def on_press(self, e):
        had_keys = self.canvas.focus_get() is self.canvas
        self.drop_held()
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
            # the picked layer's name clicked again while the list has the keyboard = renamed on let go, after the
            # double-click time (like Windows' file lists)
            self.held = (i, e.y, had_keys and i == self.picked() and a1 + 3 < e.x < m0, False)
            self.pick(i)

    def on_drag(self, e):
        """A held row dragged half a row or more: a line shows where it goes on let go."""
        if self.held is None:
            return
        i, y0, _, moved = self.held
        if not moved and abs(e.y - y0) < self.row_h / 2:
            return
        self.held = (i, y0, False, True)
        n = len(plain_layers(self.hz() or {})["layers"])
        self.drop = min(max(round(self.canvas.canvasy(e.y) / self.row_h), 0), n)
        self.redraw()

    def on_release(self, e):
        held, drop = self.held, self.drop
        self.held = self.drop = None
        if held is None:
            return
        i, _, rename, moved = held
        if moved:
            if drop is not None and drop not in (i, i + 1):
                self.move(i, drop if drop < i else drop - 1)
            else:
                self.redraw()
        elif rename:
            self.slow = self.canvas.after(DOUBLE_MS, lambda: self.slow_rename(i))

    def slow_rename(self, i):
        self.slow = None
        if self.canvas.winfo_exists() and self.box.winfo_manager() and i == self.picked():
            self.start_naming(i)

    def drop_held(self):
        """A held / dragged row let go of with no change; a slow second click's rename called off."""
        if self.slow is not None:
            self.canvas.after_cancel(self.slow)
            self.slow = None
        if self.held is not None:
            self.held = self.drop = None
            self.redraw()

    def on_double(self, e):
        self.drop_held()
        i = self.row_at(e.y)
        if i is not None and self.spots(i)[0][1] + 3 < e.x < self.spots(i)[1][0]:
            self.start_naming(i)

    def on_menu(self, e):
        i = self.row_at(e.y)
        if i is None or self.win.drag or self.held is not None:  # (a row held: nothing, like the piano roll)
            return
        self.end_naming(True)
        self.close_palette()
        self.pick(i)
        hz = self.hz()
        m = tk.Menu(self.canvas, tearoff=False)
        m.add_command(label=tr("hz.layer_rename"), command=lambda: self.start_naming(i))
        m.add_command(label=tr("hz.layer_colour"), command=lambda: self.ask_colour(i))
        to = tk.Menu(m, tearoff=False)
        for k in range(len(plain_layers(hz)["layers"])):
            if k != i:
                to.add_command(label=self.name_of(hz, k), command=lambda k=k: self.copy_sound(i, k))
        m.add_cascade(label=tr("hz.layer_copy_sound"), menu=to,
                      state="normal" if len(plain_layers(hz)["layers"]) > 1 else "disabled")
        m.add_separator()
        m.add_command(label=tr("hz.layer_up"), command=lambda: self.move(i, i - 1),
                      state="normal" if i > 0 else "disabled")
        m.add_command(label=tr("hz.layer_down"), command=lambda: self.move(i, i + 1),
                      state="normal" if i < len(plain_layers(hz)["layers"]) - 1 else "disabled")
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
        """Layer i goes, with its notes (the last one stays). No notes left in a Hz bass made with the Hz bass tool:
        it goes, like when its last note is deleted (a new one can start at the same spot)."""
        hz = self.hz()
        if hz is None or len(hz.get("layers") or ()) < 2 or not 0 <= i < len(hz["layers"]):
            return self.win.bell()
        every, info = layers_of(hz), list(hz["layers"])
        picked = hz.get("layer", 0)
        del every[i], info[i]
        picked = min(picked if picked < i else max(0, picked - 1) if picked > i else i, len(info) - 1)
        hz = with_layers(dict(hz, layers=info), every, picked)
        win, app, sh = self.win, self.app, self.win.target()
        if win.synth_win:
            win.synth_win.forget_preset()
        if all_tones(hz) or not hz_made(sh):
            return self.put(hz, tr("hz.step_layer_delete"))
        win.drop_drag()
        win.own_step = True
        try:
            app.push_undo(name=tr("hz.step_layer_delete"))
        finally:
            win.own_step = False
        app.hz_start, app.hz_defaults = left_edge(sh), hz_keys(sh)
        del app.shapes[app.sel]
        win.tones = []
        app.select(None)
        app.shapes_changed()
        app.sync_custom()
        app.schedule_autosave()
        win.sync()

    def move(self, i, j):
        """Layer i put at place j in the list (the others close up around it); the picked one stays picked. Names
        given by place ("Layer 2") are kept as they were."""
        hz = self.hz()
        n = len((hz or {}).get("layers") or ())
        if i == j or not (0 <= i < n and 0 <= j < n):
            return
        every = layers_of(hz)
        info = [dict(e, name=self.name_of(hz, k)) for k, e in enumerate(hz["layers"])]
        order = list(range(len(info)))
        order.insert(j, order.pop(i))
        if self.win.synth_win:
            self.win.synth_win.forget_preset()
        self.put(with_layers(dict(hz, layers=[info[k] for k in order]), [every[k] for k in order],
                             order.index(hz.get("layer", 0))), tr("hz.step_layer_move"))

    def change(self, i, step, **what):
        """Layer i's name / colour / mute / solo set (None = taken out)."""
        hz = self.hz()
        if hz is None:
            return
        hz = plain_layers(copy.deepcopy(hz))
        if not 0 <= i < len(hz["layers"]):
            return
        e = hz["layers"][i]
        for k, v in what.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        self.put(hz, step)

    def copy_sound(self, i, k):
        """Layer i's whole sound (knobs, effect lines, MOD...: not its notes) onto layer k."""
        hz = self.hz()
        if hz is None or not hz.get("layers"):
            return
        every = layers_of(hz)
        every[k] = dict({key: v for key, v in every[k].items() if key not in SOUND or key == "tones"},
                        **{key: copy.deepcopy(v) for key, v in every[i].items() if key in SOUND and key != "tones"})
        self.put(with_layers(hz, every), tr("hz.step_layer_sound"))

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
        self.naming = (i, entry, var, self.win.target())
        entry.bind("<Return>", lambda e: self.end_naming(True) or "break")
        entry.bind("<Escape>", lambda e: self.end_naming(False) or "break")
        entry.bind("<FocusOut>", lambda e: self.end_naming(True))

    def end_naming(self, keep):
        if self.naming is None:
            return
        i, entry, var, sh = self.naming
        self.naming = None
        name = var.get().strip()[:LAYER_NAME]
        self.canvas.delete("naming")
        entry.destroy()
        hz = self.hz()
        if (keep and hz is not None and sh is self.win.target() and i < len(plain_layers(hz)["layers"])
                and name != self.name_of(hz, i)):  # (another Hz bass shown since: nothing renamed)
            self.change(i, tr("hz.step_layer_rename"), name=name or None)
        else:
            self.redraw()
        self.canvas.focus_set()

    # ------------------------------------------------------------ colour

    def ask_colour(self, i):
        """A small popup of the piano roll's note colours under the row; a click picks one."""
        self.close_palette()
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
        top.geometry(f"+{c.winfo_rootx() + round(4 * s)}+{c.winfo_rooty() + round((i + 1) * self.row_h - c.canvasy(0))}")
        top.focus_force()

    def close_palette(self):
        if self.palette is not None:
            top, self.palette = self.palette, None
            top.destroy()
