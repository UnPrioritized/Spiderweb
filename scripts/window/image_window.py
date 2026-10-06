"""Image to notes: the image window (opened by the Picture tool, I). A picture is opened, set up with sliders (every
one a suggestion, "Back to suggested"), seen in one big preview the way a player would draw it (falling notes or a
piano roll; flat or outlined and shaded); holding the mouse on the preview or Space shows the original in its
place. Then it's dragged into the piano roll (held by its middle), placed at the play line, or placed with a click of
the Picture tool. The maths: notes/picture.py."""

import os
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import numpy as np

from files.lang import tr
from notes import picture as P

PREVIEW_W, PREVIEW_H = 900, 520  # the preview's size to start with (it grows with the window)
KB = 36  # the keyboard drawn beside the preview, in pixels
WAIT_MS = 150  # a slider moved: the picture is made again once it rests this long
SWATCH = 20


def _photo(master, rgb):
    """rows x cols x 3 floats 0..1 (sRGB) -> a Tk picture."""
    a = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    return tk.PhotoImage(master=master, data=b"P6 %d %d 255\n" % (a.shape[1], a.shape[0]) + a.tobytes(),
                         format="ppm")


def _edges(n, size):
    """Pixel i -> (cell, pixels since the cell's first pixel, pixels to its last) for `size` pixels over n cells."""
    cell = np.minimum(np.arange(size) * n // size, n - 1)
    first = np.searchsorted(cell, np.arange(n))
    last = np.append(first[1:], size) - 1
    return cell, np.arange(size) - first[cell], last[cell] - np.arange(size)


def look_picture(grid, pal_srgb, view, width, height, look="flat", outline=1, shade=True, join=True):
    """The notes as a player would draw them: width x height x 3 sRGB. Falling notes: keys are the columns, a note
    runs up a column; piano roll: keys are the rows, a note runs along a row. "outlined": a dark edge round every
    note (and between touching notes when they aren't joined) + shading across the key (bright to dark)."""
    rows, cols = grid.shape
    ry, dy0, dy1 = _edges(rows, height)
    cx, dx0, dx1 = _edges(cols, width)
    g = grid[ry][:, cx]
    out = np.zeros((height, width, 3))
    on = g >= 0
    out[on] = pal_srgb[g[on]]
    if look != "outlined":
        return out
    fall = view == "fall"
    # across the key: the key's own edges always; along time: where the note starts / ends
    if fall:
        across = (dx0[None, :] < outline) | (dx1[None, :] < outline)
        top = np.ones(grid.shape, bool)
        top[1:] = ~join | (grid[1:] != grid[:-1])
        bot = np.ones(grid.shape, bool)
        bot[:-1] = ~join | (grid[:-1] != grid[1:])
        along = ((dy0[:, None] < outline) & top[ry][:, cx]) | ((dy1[:, None] < outline) & bot[ry][:, cx])
        frac = (dx0 / np.maximum(dx0 + dx1, 1))[None, :, None]
    else:
        across = (dy0[:, None] < outline) | (dy1[:, None] < outline)
        lef = np.ones(grid.shape, bool)
        lef[:, 1:] = ~join | (grid[:, 1:] != grid[:, :-1])
        rig = np.ones(grid.shape, bool)
        rig[:, :-1] = ~join | (grid[:, :-1] != grid[:, 1:])
        along = ((dx0[None, :] < outline) & lef[ry][:, cx]) | ((dx1[None, :] < outline) & rig[ry][:, cx])
        frac = (dy0 / np.maximum(dy0 + dy1, 1))[:, None, None]
    if shade:
        out = out * (1.15 - 0.4 * frac)
    edge = (across | along) & on if outline > 0 else np.zeros_like(on)
    out[edge] = out[edge] * 0.3
    return np.clip(out, 0, 1)


class ImageWindow(tk.Toplevel):
    """One per Spiderweb (app.image_window)."""

    @classmethod
    def open(cls, app):
        w = app.image_window
        if w:
            w.deiconify()
            w.lift()
        else:
            w = app.image_window = cls(app)
        w.focus_set()
        return w

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("image.window_title"))
        self.transient(app)
        self.geometry(app.image_pos or "1500x760")
        self.pic, self.s = None, dict(P.SUGGESTED, share=1.0)
        last = app.image_last
        if last:
            self.s = P.clean_settings(last[1])
        self.locked = {}  # slot -> linear colour picked by the user
        self.cl = self.al = self.pal = self.grid = None
        self.made_for = (None, None)  # what the cells / palette were made for (only redo what changed)
        self.late, self.photo, self.held, self.view_size = None, None, False, (PREVIEW_W, PREVIEW_H)
        self.drag_mark = None
        self.build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Configure>", self.on_configure, add="+")
        self.bind("<KeyPress-space>", lambda e: (self.hold(True), "break")[1])
        self.bind("<KeyRelease-space>", lambda e: (self.hold(False), "break")[1])
        self.bind("<F1>", lambda e: (app.open_help("picture"), "break")[1])
        self.bind("<Escape>", lambda e: self.close())
        self.show_settings()
        if last and os.path.isfile(last[0]):
            self.load(last[0], quiet=True)
        self.redraw()

    # ------------------------------------------------------------ the window

    def build(self):
        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text=tr("image.made_for")).pack(side="left")
        self.view = tk.StringVar(value=self.s["view"])
        for v, t in (("fall", tr("image.falling")), ("roll", tr("image.roll_view"))):
            ttk.Radiobutton(bar, text=t, variable=self.view, value=v, command=lambda: self.put("view", self.view.get())
                            ).pack(side="left", padx=4)
        ttk.Label(bar, text=tr("image.hold_hint"), foreground="#777").pack(side="left", padx=16)
        self.what = ttk.Label(left, text=tr("image.preview_title"), font=("TkDefaultFont", 10, "bold"))
        self.what.pack(anchor="w")
        self.cv = tk.Canvas(left, width=PREVIEW_W, height=PREVIEW_H, background="black", highlightthickness=1,
                            highlightbackground="#999")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.resized(e.width, e.height))
        self.cv.bind("<ButtonPress-1>", lambda e: self.hold(True))
        self.cv.bind("<ButtonRelease-1>", lambda e: self.hold(False))
        info = ttk.Frame(left)
        info.pack(fill="x", pady=(8, 0))
        self.info = ttk.Label(info, text="", foreground="#555")
        self.info.pack(side="left")
        self.handle = tk.Label(info, text=tr("image.drag"), background="#dfe8f5", relief="ridge", padx=10, pady=6,
                               cursor="fleur")
        self.handle.pack(side="right")
        self.handle.bind("<ButtonPress-1>", self.drag_start)
        self.handle.bind("<B1-Motion>", self.drag_move)
        self.handle.bind("<ButtonRelease-1>", self.drag_end)
        ttk.Button(info, text=tr("image.place"), command=self.place_at_play_line).pack(side="right", padx=8)

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="right", fill="y")
        tabs = ttk.Notebook(side)
        tabs.pack(fill="both", expand=True)
        t1, t2 = ttk.Frame(tabs, padding=6), ttk.Frame(tabs, padding=6)
        tabs.add(t1, text=tr("image.tab_picture"))
        tabs.add(t2, text=tr("image.tab_player"))
        self.vars = {}

        f = self.section(t1, "image.picture")
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Button(r, text=tr("image.open"), command=self.ask_file).pack(side="left")
        self.name = ttk.Label(r, text=tr("image.no_picture"), foreground="#777")
        self.name.pack(side="left", padx=6)
        r = ttk.Frame(f)
        r.pack(fill="x", pady=(3, 0))
        ttk.Label(r, text=tr("image.see_through")).pack(side="left")
        self.vars["empty"] = tk.BooleanVar(value=self.s["empty"])
        for v, t in ((True, tr("image.empty")), (False, tr("image.keep"))):
            ttk.Radiobutton(r, text=t, variable=self.vars["empty"], value=v,
                            command=lambda: self.put("empty", self.vars["empty"].get())).pack(side="left", padx=4)

        f = self.section(t1, "image.adjust")
        for k in ("brightness", "contrast", "saturation"):
            self.slider(f, k, -1, 1, lambda v: "%+d" % round(v * 100) if round(v * 100) else "0")

        f = self.section(t1, "image.size")
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.keys"), width=15).pack(side="left")
        self.vars["keys"] = tk.StringVar(value=str(self.s["keys"]))
        sb = ttk.Spinbox(r, from_=2, to=256, width=6, textvariable=self.vars["keys"],
                         command=lambda: self.typed("keys"))
        sb.pack(side="left")
        sb.bind("<Return>", lambda e: self.typed("keys"))
        sb.bind("<FocusOut>", lambda e: self.typed("keys"))
        self.slider(f, "steps", 1, 8, lambda v: "%d×" % round(v), whole=True)
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.step"), width=15).pack(side="left")  # (one grid step's length)
        self.vars["step"] = tk.StringVar(value=str(self.step_ticks()))
        sb = ttk.Spinbox(r, from_=1, to=99999, width=6, textvariable=self.vars["step"],
                         command=lambda: self.typed("step"))
        sb.pack(side="left")
        sb.bind("<Return>", lambda e: self.typed("step"))
        sb.bind("<FocusOut>", lambda e: self.typed("step"))
        ttk.Label(r, text=tr("image.ticks")).pack(side="left", padx=3)

        f = self.section(t1, "image.colours")
        self.slider(f, "colours", 2, 15, lambda v: str(round(v)), whole=True, label="image.how_many")
        self.swatches = ttk.Frame(f)
        self.swatches.pack(anchor="w", pady=3)
        self.slider(f, "focus", 0, 1, None, ends=(tr("image.whole"), tr("image.details")))

        f = self.section(t1, "image.blending")
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.kind"), width=15).pack(side="left")
        kinds = [("spread", tr("image.spread")), ("pattern", tr("image.pattern")), ("none", tr("image.none"))]
        self.kind_box = ttk.Combobox(r, values=[t for _, t in kinds], width=16, state="readonly")
        self.kind_box.pack(side="left")
        self.kind_box.bind("<<ComboboxSelected>>",
                           lambda e: self.put("blend", kinds[self.kind_box.current()][0]))
        self.kinds = kinds
        pct = lambda v: "%d%%" % round(v * 100)  # noqa: E731
        self.slider(f, "strength", 0, 1, pct)
        self.slider(f, "keep", 0, 1, pct, label="image.keep_details")
        self.slider(f, "sharpen", 0, 1, pct)
        ttk.Button(t1, text=tr("image.suggested"), command=self.suggested).pack(anchor="e", pady=(4, 0))

        f = self.section(t2, "image.look")
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.preset"), width=15).pack(side="left")
        looks = [("flat", tr("image.flat")), ("outlined", tr("image.outlined"))]
        self.look_box = ttk.Combobox(r, values=[t for _, t in looks], width=22, state="readonly")
        self.look_box.pack(side="left")
        self.look_box.bind("<<ComboboxSelected>>", lambda e: self.put("look", looks[self.look_box.current()][0]))
        self.looks = looks
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.outline"), width=15).pack(side="left")
        self.vars["outline"] = tk.StringVar(value=str(self.s["outline"]))
        sb = ttk.Spinbox(r, from_=0, to=8, width=6, textvariable=self.vars["outline"],
                         command=lambda: self.typed("outline"))
        sb.pack(side="left")
        sb.bind("<Return>", lambda e: self.typed("outline"))
        ttk.Label(r, text=tr("image.px")).pack(side="left", padx=3)
        for k in ("shade", "join"):
            self.vars[k] = tk.BooleanVar(value=self.s[k])
            ttk.Checkbutton(f, text=tr("image." + k), variable=self.vars[k],
                            command=lambda k=k: self.put(k, self.vars[k].get())).pack(anchor="w", pady=1)
        ttk.Label(t2, text=tr("image.look_note"), foreground="#777", wraplength=330, justify="left").pack(
            anchor="w", pady=(6, 0))

    def section(self, parent, key):
        f = ttk.LabelFrame(parent, text=tr(key), padding=(6, 2, 6, 4))
        f.pack(fill="x", pady=(0, 5))
        return f

    def slider(self, f, key, lo, hi, shown, whole=False, ends=None, label=None):
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr(label or "image." + key), width=15).pack(side="left")
        var = self.vars[key] = tk.DoubleVar(value=self.s[key])
        label = None
        if ends:
            ttk.Label(r, text=ends[0], foreground="#777").pack(side="left")

        def moved(v):
            v = round(float(v)) if whole else round(float(v), 3)
            if label is not None:
                label.config(text=shown(v))
            if v != self.s[key]:
                self.put(key, v, later=True)

        ttk.Scale(r, from_=lo, to=hi, variable=var, length=150 if not ends else 110, command=moved).pack(
            side="left", padx=3)
        if ends:
            ttk.Label(r, text=ends[1], foreground="#777").pack(side="left")
        else:
            label = ttk.Label(r, text=shown(self.s[key]), width=6)
            label.pack(side="left")
        self.vars[key + "_label"] = (label, shown)

    def show_settings(self):
        """Every control shows self.s."""
        s = self.s
        for k, v in self.vars.items():
            if k.endswith("_label"):
                continue
            if k == "step":
                v.set(str(self.step_ticks()))
            elif k in s:
                v.set(s[k] if not isinstance(v, tk.StringVar) else str(s[k]))
            lab = self.vars.get(k + "_label")
            if lab and lab[0] is not None:
                lab[0].config(text=lab[1](s[k]))
        self.view.set(s["view"])
        self.kind_box.current([k for k, _ in self.kinds].index(s["blend"]))
        self.look_box.current([k for k, _ in self.looks].index(s["look"]))

    def step_ticks(self):
        return max(1, round(self.s["step"] * self.app.ppq))

    def typed(self, key):
        try:
            n = int(float(self.vars[key].get()))
        except ValueError:
            self.show_settings()
            return
        if key == "step":
            self.put("step", max(1, n) / self.app.ppq)
        elif key == "keys":
            self.put("keys", max(2, min(self.app.keys, n)))
        else:
            self.put(key, max(0, min(8, n)))
        self.show_settings()

    def put(self, key, value, later=False):
        """A setting changed: the picture is made again (a slider: once it rests)."""
        self.s[key] = value
        if self.late:
            self.after_cancel(self.late)
        self.late = self.after(WAIT_MS if later else 1, self.remake)

    def suggested(self):
        keep = {k: self.s[k] for k in ("view", "look", "outline", "shade", "join", "step")}
        self.s = dict(P.SUGGESTED, share=1.0, **keep)
        self.locked = {}
        self.show_settings()
        self.remake()

    # ------------------------------------------------------------ the picture

    def ask_file(self):
        path = filedialog.askopenfilename(parent=self, title=tr("image.open_title"), filetypes=[
            (tr("image.files"), "*.png *.gif"), (tr("image.all_files"), "*.*")])
        if path:
            self.load(path)

    def load(self, path, quiet=False):
        try:
            pic = P.load(path, self)
        except (tk.TclError, OSError, ValueError) as e:
            if not quiet:
                messagebox.showerror(tr("image.window_title"), tr("image.cant_open", e=e), parent=self)
            return False
        self.pic = pic
        self.locked = {}
        self.made_for = (None, None)
        self.name.config(text=os.path.basename(path), foreground="#2a7")
        self.app.image_last = (path, dict(self.s))
        self.remake()
        return True

    def remake(self):
        """Cells -> colours -> grid, each only when something it depends on changed."""
        self.late = None
        if not self.pic:
            self.redraw()
            return
        self.info.config(text=tr("image.working"))
        self.update_idletasks()
        s = self.s
        cells_key = (s["keys"], s["steps"], s["view"], s["brightness"], s["contrast"], s["saturation"],
                     s["sharpen"])
        if self.made_for[0] != cells_key:
            rows, cols = P.grid_size(self.pic, s["keys"], s["steps"], s["view"])
            cl, al = P.cells(self.pic, rows, cols)
            self.cl, self.al = P.adjust(cl, s["brightness"], s["contrast"], s["saturation"], s["sharpen"]), al
        pal_key = (cells_key, s["colours"], s["focus"], s["empty"], tuple(sorted(
            (k, tuple(np.round(v, 6))) for k, v in self.locked.items())))
        if self.made_for[1] != pal_key:
            al = self.al if s["empty"] else None
            self.pal = P.fit_palette([(self.cl, al, 1.0, s["focus"])], s["colours"],
                                     locked=[(k, v) for k, v in self.locked.items() if k < s["colours"]])
        self.made_for = (cells_key, pal_key)
        self.grid = P.quantise(self.cl, self.pal, s["blend"], s["strength"], s["keep"], self.al, s["empty"])
        self.app.image_last = (self.pic.path, dict(s))
        self.show_swatches()
        self.redraw()

    def show_swatches(self):
        for w in self.swatches.winfo_children():
            w.destroy()
        if self.pal is None:
            return
        for k, c in enumerate(self.pal):
            cv = tk.Canvas(self.swatches, width=SWATCH, height=SWATCH, highlightthickness=1,
                           highlightbackground="#555", background="#" + P.hex_of(c))
            cv.pack(side="left", padx=1)
            if k in self.locked:  # (picked by hand: a small corner mark)
                cv.create_polygon(0, 0, 8, 0, 0, 8, fill="white", outline="black")
            cv.bind("<ButtonRelease-1>", lambda e, k=k: self.pick_colour(k))
            cv.bind("<ButtonRelease-3>", lambda e, k=k: self.free_colour(k))
        from window.widgets import Tooltip
        Tooltip(self.swatches, tr("image.swatch_tip"))

    def pick_colour(self, k):
        got = colorchooser.askcolor(color="#" + P.hex_of(self.pal[k]), parent=self, title=tr("image.pick_colour"))
        if got and got[1]:
            self.locked[k] = P.lin_of(got[1][1:].upper())
            self.remake()

    def free_colour(self, k):
        if self.locked.pop(k, None) is not None:
            self.remake()

    # ------------------------------------------------------------ the preview

    def resized(self, w, h):
        if (w, h) != self.view_size:
            self.view_size = (w, h)
            self.redraw()

    def hold(self, on):
        """Mouse held on the preview / Space held: the original in its place."""
        if on != self.held:
            self.held = on
            self.redraw()

    def fit(self):
        """Where the picture goes in the canvas: (x, y, width, height), keyboard space left out."""
        cw, ch = self.view_size
        fall = self.s["view"] == "fall"
        aw, ah = (cw - 8, ch - KB - 8) if fall else (cw - KB - 8, ch - 8)
        asp = self.pic.aspect if self.pic else 16 / 9
        w = min(aw, ah * asp)
        h = w / asp
        w, h = max(8, int(w)), max(8, int(h))
        x = (cw - w - (0 if fall else KB)) // 2 + (0 if fall else KB)
        y = (ch - h - (KB if fall else 0)) // 2
        return x, y, w, h

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        if not self.pic or self.grid is None:
            cv.create_text(self.view_size[0] // 2, self.view_size[1] // 2, text=tr("image.no_picture"),
                           fill="#aaa")
            self.info.config(text="")
            return
        x, y, w, h = self.fit()
        s = self.s
        if self.held:
            rgb = P.to_srgb(P.cells(self.pic, h, w)[0])
            self.what.config(text=tr("image.original_title"))
        else:
            rgb = look_picture(self.grid, P.to_srgb(self.pal), s["view"], w, h, s["look"], s["outline"],
                               s["shade"], s["join"])
            self.what.config(text=tr("image.preview_title"))
        self.photo = _photo(self, rgb)
        cv.create_image(x, y, image=self.photo, anchor="nw")
        self.draw_keys(x, y, w, h)
        rows, steps, keys = P.grid_notes(self.grid, s["view"])
        if not s["join"]:
            steps = steps
        notes = len(rows) if s["join"] else int((self.grid >= 0).sum())
        self.info.config(text=tr("image.info", keys=keys, steps=steps, notes=f"{notes:,}",
                                 used=len(np.unique(self.grid[self.grid >= 0])), colours=len(self.pal)))

    def draw_keys(self, x, y, w, h):
        """A keyboard under (falling) / left of (piano roll) the picture, one key per grid key."""
        cv, keys = self.cv, self.s["keys"]
        if self.s["view"] == "fall":
            top, kw = y + h + 2, w / keys
            cv.create_rectangle(x, top, x + w, top + KB - 4, fill="#e8e8e8", outline="")
            for k in range(keys):
                if k % 12 in (1, 3, 6, 8, 10):
                    cv.create_rectangle(x + k * kw, top, x + (k + 1) * kw, top + (KB - 4) * 0.6, fill="#222",
                                        outline="")
        else:
            left, kh = x - KB, h / keys
            cv.create_rectangle(left, y, x - 2, y + h, fill="#e8e8e8", outline="")
            for k in range(keys):
                if k % 12 in (1, 3, 6, 8, 10):
                    yy = y + h - (k + 1) * kh
                    cv.create_rectangle(left, yy, left + (KB - 2) * 0.6, yy + kh, fill="#222", outline="")

    # ------------------------------------------------------------ placing

    def placed_shape(self, beat, key):
        """The picture as a new shape centred on (beat, key), or None (no picture / nothing coloured)."""
        if not self.pic or self.grid is None:
            return None
        s = self.s
        info = {"file": self.pic.path, "sig": self.pic.sig, "size": list(self.pic.size),
                "set": dict(P.clean_settings(s), pal=[P.hex_of(c) for c in self.pal], locked=sorted(self.locked))}
        _, steps, keys = P.grid_notes(self.grid, s["view"])
        vel = self.app.defaults.get("vel0", 127)
        return P.picture_shape(self.grid, info, beat - steps * s["step"] / 2, round(key - (keys - 1) / 2),
                               s["step"], vel)

    def place(self, beat, key):
        sh = self.placed_shape(beat, key)
        if sh is None:
            self.app.status.config(text=tr("image.nothing_to_place"))
            return False
        self.app.add_picture(sh)
        return True

    def place_at_play_line(self):
        app = self.app
        roll = app.roll
        mid_key = roll.y2p(roll.winfo_height() / 2)
        _, steps, _ = P.grid_notes(self.grid, self.s["view"]) if self.grid is not None else (0, 0, 0)
        self.place(app.playhead + steps * self.s["step"] / 2, mid_key)

    def drag_start(self, e):
        self.drag_mark = None

    def roll_spot(self, e):
        """Where on the main piano roll the mouse is (beat, key), or None when it's not over it."""
        roll = self.app.roll
        x, y = e.x_root - roll.winfo_rootx(), e.y_root - roll.winfo_rooty()
        if 0 <= x < roll.winfo_width() and 0 <= y < roll.winfo_height() and x > roll.kb_w:
            return roll.x2t(x), roll.y2p(y)
        return None

    def drag_move(self, e):
        roll = self.app.roll
        roll.delete("picdrag")
        spot = self.roll_spot(e)
        if spot is None or self.grid is None:
            return
        _, steps, keys = P.grid_notes(self.grid, self.s["view"])
        b, k = spot
        half_b, half_k = steps * self.s["step"] / 2, keys / 2
        roll.create_rectangle(roll.t2x(b - half_b), roll.p2y(k + half_k), roll.t2x(b + half_b),
                              roll.p2y(k - half_k), outline="#e02020", dash=(4, 3), width=2, tags="picdrag")

    def drag_end(self, e):
        self.app.roll.delete("picdrag")
        spot = self.roll_spot(e)
        if spot is not None:
            self.place(*spot)

    # ------------------------------------------------------------ closing

    def on_configure(self, e):
        if e.widget is self:
            self.app.image_pos = self.geometry()

    def close(self):
        if self.late:
            self.after_cancel(self.late)
        self.app.image_window = None
        self.destroy()
        self.app.roll.focus_set()
