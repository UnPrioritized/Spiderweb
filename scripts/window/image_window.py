"""Image to notes: the image window (opened by the Picture tool, I). A picture is opened, set up with sliders (every
one a suggestion, "Back to suggested"), seen in one big preview the way a player would draw it (falling notes or a
piano roll; flat or outlined and shaded); holding the mouse on the preview or Space shows the original in its
place. Then it's dragged into the piano roll (held by its middle), placed at the play line, or placed with a click of
the Picture tool. The maths: notes/picture.py."""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import numpy as np

from files import speed
from files.lang import tr
from files.speed import Photo
from notes import picture as P
from window.widgets import Tooltip

PREVIEW_W, PREVIEW_H = 900, 520  # the preview's size to start with (it grows with the window)
KB = 36  # the keyboard drawn beside the preview, in pixels
WAIT_MS = 150  # a slider moved: the picture is made again once it rests this long
SWATCH = 20
BOXES = {"keys": (2, 256), "step": (1, 99999), "outline": (0, 8), "player_w": (100, 20000), "player_h": (100, 20000),
         "player_keys": (1, 256)}  # the number boxes: lowest, highest (keys: the piano roll's keys at most)
LOOK_ONLY = ("look", "outline", "shade", "join", "player_w", "player_h", "player_keys")  # (only the preview changes)
THREAD_CELLS = 100_000  # a grid with more cells is made in the background (progress bar + Cancel); ~0.4 s
BIG_CELLS = 1_000_000  # ... and with more than this asked first (user's soft limit: ~4 s, up to that many notes)
POLL_MS = 50


def _photo(master, rgb):
    """rows x cols x 3 floats 0..1 (sRGB) -> a speed.Photo (keep it while its .photo is shown)."""
    a = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    pic = Photo(master, a.shape[1], a.shape[0])
    pic.put(a)
    return pic


def _kinds():
    """The picture kinds this Spiderweb reads, in words."""
    return tr("image.kinds_all") if speed.pillow() else tr("image.kinds_basic")


def _edges(n, size):
    """Pixel i -> (cell, pixels since the cell's first pixel, pixels to its last) for `size` pixels over n cells."""
    cell = np.minimum(np.arange(size) * n // size, n - 1)
    first = np.searchsorted(cell, np.arange(n))
    last = np.append(first[1:], size) - 1
    return cell, np.arange(size) - first[cell], last[cell] - np.arange(size)


def look_picture(grid, pal_srgb, view, width, height, look="flat", outline=1, shade=True, join=True):
    """The notes as a player would draw them: width x height x 3 sRGB. Falling notes: keys are the columns, a note
    runs up a column; piano roll: keys are the rows, a note runs along a row. "outlined": a dark edge round every
    note (and between touching notes when they aren't joined) + shading across the key (bright to dark). outline =
    its width in this picture's pixels (any fraction: the last pixel only partly dark)."""
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

    def dark(d):  # how much of the pixel d pixels in from an edge the outline covers
        return np.clip(outline - d, 0, 1)

    # across the key: the key's own edges always; along time: where the note starts / ends
    if fall:
        across = np.maximum(dark(dx0), dark(dx1))[None, :]
        top = np.ones(grid.shape, bool)
        top[1:] = ~join | (grid[1:] != grid[:-1])
        bot = np.ones(grid.shape, bool)
        bot[:-1] = ~join | (grid[:-1] != grid[1:])
        along = np.maximum(dark(dy0)[:, None] * top[ry][:, cx], dark(dy1)[:, None] * bot[ry][:, cx])
        frac = (dx0 / np.maximum(dx0 + dx1, 1))[None, :, None]
    else:
        across = np.maximum(dark(dy0), dark(dy1))[:, None]
        lef = np.ones(grid.shape, bool)
        lef[:, 1:] = ~join | (grid[:, 1:] != grid[:, :-1])
        rig = np.ones(grid.shape, bool)
        rig[:, :-1] = ~join | (grid[:, :-1] != grid[:, 1:])
        along = np.maximum(dark(dx0)[None, :] * lef[ry][:, cx], dark(dx1)[None, :] * rig[ry][:, cx])
        frac = (dy0 / np.maximum(dy0 + dy1, 1))[:, None, None]
    if shade:
        out = out * (1.15 - 0.4 * frac)
    edge = np.maximum(across, along) * on
    out = out * (1 - 0.7 * edge)[..., None]
    return np.clip(out, 0, 1)


class Making:
    """One picture being made: cells -> colours -> grid, from what it was given (in the background when it's big:
    it touches no window). cells / pal: already made ones to reuse, or None. Cancel: cancel = True (the next tick
    stops it)."""

    def __init__(self, pic, s, size, cells_key, pal_key, cells, pal, others, locked, start, editing):
        self.pic, self.s, self.size, self.cells_key, self.pal_key = pic, s, size, cells_key, pal_key
        self.cells, self.pal, self.others, self.locked, self.start = cells, pal, others, locked, start
        self.editing = editing
        self.grid, self.cancel, self.done, self.progress, self.error, self.seconds = None, False, False, 0.0, None, 0.0

    def tick(self, f):
        if self.cancel:
            raise P.Cancelled
        self.progress = f

    def run(self):
        s, started = self.s, time.perf_counter()
        try:
            if self.cells is None:
                cl, al = P.cells(self.pic, *self.size)
                self.cells = (P.adjust(cl, s["brightness"], s["contrast"], s["saturation"], s["sharpen"]), al)
            self.tick(0.05)
            cl, al = self.cells
            if self.pal is None:
                self.pal = P.fit_palette(
                    [(cl, al if s["empty"] else None, s["share"] if self.others else 1.0, s["focus"])] + self.others,
                    s["colours"], locked=self.locked, start=self.start, tick=lambda f: self.tick(0.05 + 0.6 * f))
            a = self.progress
            self.grid = P.quantise(cl, self.pal, s["blend"], s["strength"], s["keep"], al, s["empty"],
                                   tick=lambda f: self.tick(a + (1 - a) * f))
            self.seconds = time.perf_counter() - started
        except P.Cancelled:
            self.cancel = True
        except Exception as e:  # (shown / raised by the window: ImageWindow.finished)
            self.error = e
        finally:
            self.done = True


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
        self.sliding = None  # (slider key, its value at the press) while the mouse holds a slider
        self._other_cells = {}  # the other placed pictures' cells (others_cells)
        self.made = None  # (picture, settings, editing) the shown grid / colours were made from
        self.making = None  # the Making in the background, if any
        self.after_made = None  # what to do once the picture being made is done (not when it's cancelled)
        self.big_yes = None  # (picture fingerprint, rows, cols) said Yes to (big_ok)
        self.build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Configure>", self.on_configure, add="+")
        self.bind("<KeyPress-space>", lambda e: (self.hold(True), "break")[1])
        self.bind("<KeyRelease-space>", lambda e: (self.hold(False), "break")[1])
        self.bind("<F1>", lambda e: (app.open_help("picture"), "break")[1])
        self.bind("<Escape>", lambda e: self.cancel_making() if self.making else self.close())
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
        info.pack(side="bottom", fill="x", pady=(8, 0), before=self.cv)  # (a short window shrinks the preview)
        self.info = ttk.Label(info, text="", foreground="#555")
        self.info.pack(side="left")
        self.prog = ttk.Frame(left, padding=8, borderwidth=1, relief="solid")  # (on the preview while a big picture is made:
        self.prog_text = ttk.Label(self.prog, text="")             # show_bar; nothing moves, user)
        self.prog_text.pack(anchor="w")
        self.cancel_btn = ttk.Button(self.prog, text=tr("image.cancel"), command=self.cancel_making)
        self.cancel_btn.pack(side="right", pady=(6, 0))
        self.bar = ttk.Progressbar(self.prog, length=90, maximum=100)
        self.bar.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=(6, 0))
        self.handle = tk.Label(info, text=tr("image.drag"), background="#dfe8f5", relief="ridge", padx=10, pady=6,
                               cursor="fleur")
        self.handle.pack(side="right")
        self.handle.bind("<ButtonPress-1>", self.drag_start)
        self.handle.bind("<B1-Motion>", self.drag_move)
        self.handle.bind("<ButtonRelease-1>", self.drag_end)
        ttk.Button(info, text=tr("image.place"), command=self.place_at_play_line).pack(side="right", padx=8)
        self.apply_btn = ttk.Button(info, text=tr("image.apply"), command=self.apply)  # (packed while editing)
        self.editing = None  # the placed picture this window changes: (its shape, number, picture id), or None

        # the settings: scroll (scrollbar, mouse wheel) when the window is too short for them (user)
        box = ttk.Frame(body, padding=(10, 0, 0, 0))
        box.pack(side="right", fill="y", before=left)  # (first: a narrow window shrinks the preview, not these)
        c = self.side_canvas = tk.Canvas(box, highlightthickness=0, bd=0, width=1, height=1,
                                         bg=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
        self.side_bar = ttk.Scrollbar(box, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.side_bar.set)
        c.pack(side="left", fill="y")
        side = self.side = ttk.Frame(c)
        self._side_win = c.create_window(0, 0, window=side, anchor="nw")
        c.bind("<Configure>", lambda e: self.fit_side())
        side.bind("<Configure>", lambda e: self.after_idle(self.fit_side))
        self.bind("<MouseWheel>", self.side_wheel, add="+")
        tabs = self.tabs = ttk.Notebook(side)
        tabs.pack(fill="both", expand=True)
        t1, t2 = ttk.Frame(tabs, padding=6), ttk.Frame(tabs, padding=6)
        tabs.add(t1, text=tr("image.tab_picture"))
        tabs.add(t2, text=tr("image.tab_player"))
        self.vars, self.scales = {}, {}

        f = self.section(t1, "image.picture")
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Button(r, text=tr("image.open"), command=self.ask_file).pack(side="left")
        self.name = ttk.Label(r, text=tr("image.no_picture", kinds=_kinds()), foreground="#777")
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
        self.number_box(r, "keys")
        self.slider(f, "steps", 1, 8, lambda v: "%d×" % round(v), whole=True)
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.step"), width=15).pack(side="left")  # (one grid step's length)
        self.number_box(r, "step")
        ttk.Label(r, text=tr("image.ticks")).pack(side="left", padx=3)

        f = self.section(t1, "image.colours")
        self.slider(f, "colours", 2, 15, lambda v: str(round(v)), whole=True, label="image.how_many")
        self.vars["use10"] = tk.BooleanVar(value=self.s["use10"])
        use10 = ttk.Checkbutton(f, text=tr("image.use10"), variable=self.vars["use10"],  # (user: here, not Player look)
                                command=lambda: self.set_use10(self.vars["use10"].get()))
        use10.pack(anchor="w", pady=(2, 0))
        Tooltip(use10, tr("image.use10_tip"))
        self.swatches = ttk.Frame(f)
        self.swatches.pack(anchor="w", pady=3)
        Tooltip(self.swatches, tr("image.swatch_tip"))
        self.slider(f, "focus", 0, 1, None, ends=(tr("image.whole"), tr("image.details")))
        self.slider(f, "share", 0.25, 4, lambda v: "%.1f×" % v)  # (only matters with other pictures placed)
        self.share_note = ttk.Label(f, text="", foreground="#777", wraplength=330, justify="left")
        self.share_note.pack(anchor="w")

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
        self.number_box(r, "outline")
        ttk.Label(r, text=tr("image.px")).pack(side="left", padx=3)
        # the player's window: an outline of so many px looks thinner in a smaller preview (user)
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        Tooltip(ttk.Label(r, text=tr("image.player_window"), width=15), tr("image.player_tip")).widget.pack(
            side="left")
        self.number_box(r, "player_w")
        ttk.Label(r, text="×").pack(side="left", padx=2)
        self.number_box(r, "player_h")
        ttk.Label(r, text=tr("image.px")).pack(side="left", padx=3)
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        Tooltip(ttk.Label(r, text=tr("image.player_keys"), width=15), tr("image.player_tip")).widget.pack(
            side="left")
        self.number_box(r, "player_keys")
        for k in ("shade", "join"):
            self.vars[k] = tk.BooleanVar(value=self.s[k])
            ttk.Checkbutton(f, text=tr("image." + k), variable=self.vars[k],
                            command=lambda k=k: self.put(k, self.vars[k].get())).pack(anchor="w", pady=1)
        ttk.Label(t2, text=tr("image.look_note"), foreground="#777", wraplength=330, justify="left").pack(
            anchor="w", pady=(0, 6))

        f = ttk.LabelFrame(t2, text=tr("image.colour_list"), padding=(6, 2, 6, 4))
        f.pack(fill="x", pady=(0, 5))
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.format"), width=15).pack(side="left")
        self.fmt_box = ttk.Combobox(r, width=24, state="readonly")
        self.fmt_box.pack(side="left")
        self.fmt_box.bind("<<ComboboxSelected>>", lambda e: self.set_fmt(self.fmt_box.get()))
        r = ttk.Frame(f)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text=tr("image.by"), width=15).pack(side="left")
        bys = [("channel", tr("image.by_channel")), ("order", tr("image.by_order"))]
        self.by_box = ttk.Combobox(r, values=[t for _, t in bys], width=24, state="readonly")
        self.by_box.pack(side="left")
        self.by_box.bind("<<ComboboxSelected>>", lambda e: self.set_by(bys[self.by_box.current()][0]))
        self.bys = bys
        r = ttk.Frame(f)
        r.pack(fill="x", pady=(4, 2))
        ttk.Button(r, text=tr("image.copy"), command=self.copy_colours).pack(side="left")
        ttk.Button(r, text=tr("image.paste"), command=self.paste_colours).pack(side="left", padx=4)
        ttk.Button(r, text=tr("image.edit_formats"), command=self.edit_formats).pack(side="left")
        ttk.Label(f, text=tr("image.copy_gives"), foreground="#555").pack(anchor="w", pady=(4, 0))
        self.gives = tk.Text(f, width=40, height=7, font=("Consolas", 9), background="#fafafa", wrap="none")
        self.gives.pack(fill="x")
        self.gives.bind("<ButtonPress-1>", lambda e: self.gives.focus_set(), add="+")  # (so Ctrl+C copies its text)
        self.gives.tag_config("bad", foreground="white", background="#d33")
        self.start = {}  # pasted colours that are only a starting point: slot -> linear colour

    def fit_side(self):
        """Like the main window's side panel (App.fit_side): as wide as the settings, as tall as the window; the
        scrollbar only while they don't fit."""
        if not self.winfo_exists():
            return
        c, side = self.side_canvas, self.side
        need, have = side.winfo_reqheight(), c.winfo_height()
        if int(c.cget("width")) != side.winfo_reqwidth():
            c.config(width=side.winfo_reqwidth())
        c.itemconfigure(self._side_win, width=side.winfo_reqwidth(), height=have if need < have else 0)
        c.configure(scrollregion=(0, 0, side.winfo_reqwidth(), max(need, have)))
        if need > have + 1:
            if not self.side_bar.winfo_ismapped():
                self.side_bar.pack(side="right", fill="y", before=c)
        elif self.side_bar.winfo_ismapped():
            self.side_bar.pack_forget()
            c.yview_moveto(0)

    def side_wheel(self, e):
        """The mouse wheel over the settings scrolls them (boxes and lists that scroll themselves keep it)."""
        if (str(e.widget).startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and not isinstance(e.widget, (tk.Listbox, tk.Text, ttk.Combobox, ttk.Spinbox))):
            self.side_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def number_box(self, parent, key):
        """A box for a whole number (BOXES: its limits), taken on Enter / the arrows / leaving it."""
        lo, hi = BOXES[key]
        self.vars[key] = tk.StringVar(value=str(self.step_ticks() if key == "step" else self.s[key]))
        sb = ttk.Spinbox(parent, from_=lo, to=hi, width=6, textvariable=self.vars[key],
                         command=lambda: self.typed(key))
        sb.pack(side="left")
        sb.bind("<Return>", lambda e: self.typed(key))
        sb.bind("<FocusOut>", lambda e: self.typed(key))
        return sb

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
                if self.sliding:  # (held by the mouse: made again when let go, user found it laggy)
                    self.s[key] = v
                else:
                    self.put(key, v, later=True)

        def press(e):
            self.sliding = (key, self.s[key])
            if "slider" in str(scale.identify(e.x, e.y)):
                return None
            # beside the knob: it jumps to the mouse and follows it from there (not a step at a time)
            var.set(scale.get(e.x, e.y))
            moved(var.get())
            try:
                self.tk.eval("set ::ttk::scale::State(dragging) 1; set ::ttk::scale::State(xoffset) 0;"
                             " set ::ttk::scale::State(yoffset) 0")
            except tk.TclError:
                return None
            return "break"

        def release(e):
            if self.sliding and self.sliding[0] == key:
                was = self.sliding[1]
                self.sliding = None
                if self.s[key] != was:
                    self.put(key, self.s[key])

        def reset(e):  # (double-click: back to its suggested value, user)
            self.sliding = None
            var.set(P.SUGGESTED.get(key, 1.0))
            moved(var.get())
            return "break"

        scale = ttk.Scale(r, from_=lo, to=hi, variable=var, length=150 if not ends else 110, command=moved)
        scale.pack(side="left", padx=3)
        scale.bind("<ButtonPress-1>", press)
        scale.bind("<ButtonRelease-1>", release, add="+")
        scale.bind("<Double-Button-1>", reset)
        self.scales[key] = scale
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
        self.by_box.current([k for k, _ in self.bys].index(s["by"]))
        self.fmt_box.config(values=[f["name"] for f in self.formats()])
        self.fmt_box.set(self.format()["name"])
        self.scales["colours"].config(to=16 if s["use10"] else 15)
        n = len(self.others())
        self.share_note.config(text=tr("image.share_note", n=n) if n else tr("image.share_alone"))

    # ------------------------------------------------------------ the colour list

    # ------------------------------------------------------------ other placed pictures (they share the colours)

    def others(self):
        """The other placed pictures' shape numbers (one set of colours for all of them, user)."""
        skip = self.edited()
        return [i for i, sh in enumerate(self.app.shapes) if "picture" in sh and i != skip]

    def others_cells(self):
        """[(cells, alpha, share, focus)] of the other placed pictures whose files are there, and a key that changes
        when any of them does."""
        out, key = [], []
        for i in self.others():
            p = self.app.shapes[i]["picture"]
            s = p["set"]
            pic = self.app.picture_for(p)
            if pic is None:
                continue
            steps, keys = p["grid"]
            rows, cols = (steps, keys) if s["view"] == "fall" else (keys, steps)
            k = (p["file"], p["sig"], rows, cols, s["brightness"], s["contrast"], s["saturation"], s["sharpen"],
                 s["empty"])
            got = self._other_cells.get(k)
            if got is None:
                cl, al = P.cells(pic, rows, cols)
                cl = P.adjust(cl, s["brightness"], s["contrast"], s["saturation"], s["sharpen"])
                got = self._other_cells[k] = (cl, al if s["empty"] else None)
            out.append((got[0], got[1], s.get("share", 1.0), s["focus"]))
            key.append((k, s.get("share", 1.0), s["focus"]))
        return out, tuple(key)

    def project_locks(self):
        """The colours picked by hand for the placed pictures (they share them): slot -> linear colour."""
        for i in self.others():
            s = self.app.shapes[i]["picture"]["set"]
            pal = s.get("pal") or []
            return {k: P.lin_of(pal[k]) for k in s.get("locked", []) if k < len(pal)}
        return {}

    def formats(self):
        from files import colour_list as CL
        return CL.built_in() + CL.load_formats()

    def format(self):
        """The format picked (the first built-in one if its name isn't there any more)."""
        all_ = self.formats()
        return next((f for f in all_ if f["name"] == self.s.get("fmt")), all_[0])

    def colours_hex(self):
        return [P.hex_of(c) for c in self.pal] if self.pal is not None else []

    def set_fmt(self, name):
        self.s["fmt"] = name
        self.show_gives()

    def set_by(self, by):
        self.s["by"] = by
        self.show_gives()

    def show_gives(self):
        """The "What Copy gives" box."""
        from files import colour_list as CL
        t = self.gives
        t.config(state="normal")
        t.delete("1.0", "end")
        for text, ok in CL.write(self.format(), self.colours_hex(), self.s["by"], self.s["use10"]):
            t.insert("end", text, () if ok else ("bad",))
        t.config(state="disabled")

    def set_use10(self, on):
        self.s["use10"] = on
        if not on and self.s["colours"] > 15:
            self.s["colours"] = 15
        self.show_settings()
        self.show_gives()
        self.put("use10", on)

    def copy_colours(self):
        from window.format_window import copy_colours
        if self.pal is not None and copy_colours(self, self.format(), self.colours_hex(), self.s["by"],
                                                 self.s["use10"]):
            self.app.status.config(text=tr("image.copied", name=self.format()["name"]))

    def edit_formats(self):
        from window.format_window import FormatWindow

        def done(name):
            if name:
                self.s["fmt"] = name
            self.show_settings()
            self.show_gives()

        FormatWindow(self, self.colours_hex, lambda: self.s["by"], lambda: self.s["use10"], self.format()["name"],
                     done)

    def paste_colours(self):
        from window.paste_window import PasteWindow
        PasteWindow(self, self.s["by"], self.s["use10"], self.s["colours"], self.pasted)

    def pasted(self, slots, lock, alpha):
        """Paste colours' "Use these colours": the picture takes them (locked, or as a starting point)."""
        n = max(2, min(16 if self.s["use10"] else 15, max(slots) + 1))
        self.s["colours"] = n
        cols = {k: P.lin_of(h) for k, h in slots.items() if k < n}
        if lock:
            self.locked, self.start = cols, {}
        else:
            self.locked, self.start = {}, cols
        self.show_settings()
        self.made_for = (self.made_for[0], None)
        self.remake()
        self.app.status.config(text=tr("image.pasted", n=len(cols)))

    def step_ticks(self):
        return max(1, round(self.s["step"] * self.app.ppq))

    def typed(self, key):
        try:
            n = int(float(self.vars[key].get()))
        except ValueError:
            self.show_settings()
            return
        lo, hi = BOXES[key]
        n = max(lo, min(self.app.keys if key == "keys" else hi, n))
        if key == "step":
            if n != self.step_ticks():
                self.put("step", n / self.app.ppq)
        elif n != self.s[key]:
            self.put(key, n)
        self.show_settings()

    def take_typed(self):
        """Before placing / applying: numbers typed in the Keys / One step / Outline boxes without Enter are taken,
        and a picture still waiting to be made again (a slider just moved) is made now."""
        try:
            box = self.focus_get()
        except (KeyError, tk.TclError):  # (the keyboard in a pop-up menu / another program)
            box = None
        if isinstance(box, ttk.Spinbox):  # (a box takes its number when it loses the keyboard: only this one can wait)
            key = next((k for k in BOXES if str(box.cget("textvariable")) == str(self.vars[k])), None)
            shown = key and (str(self.step_ticks()) if key == "step" else str(self.s[key]))
            if key and self.vars[key].get().strip() != shown:
                self.typed(key)
        if self.late:
            self.after_cancel(self.late)
            self.remake()

    def put(self, key, value, later=False):
        """A setting changed: the picture is made again (a slider: once it rests); a look setting: drawn again."""
        self.s[key] = value
        if key in LOOK_ONLY:
            if self.pic:
                self.app.image_last = (self.pic.path, dict(self.s))
            self.redraw()
            return
        if self.late:
            self.after_cancel(self.late)
        self.late = self.after(WAIT_MS if later else 1, self.remake)

    def suggested(self):
        keep = {k: self.s[k] for k in ("view", "step") + LOOK_ONLY}
        self.s = dict(P.SUGGESTED, share=1.0, **keep)
        self.locked, self.start = {}, {}
        self.show_settings()
        self.remake()

    # ------------------------------------------------------------ the picture

    def ask_file(self):
        path = filedialog.askopenfilename(parent=self, title=tr("image.open_title"), filetypes=[
            (tr("image.files"), P.file_patterns()), (tr("image.all_files"), "*.*")])
        if path:
            self.load(path)

    def load(self, path, quiet=False, then=None):
        """Opens a picture file and makes it (then: done once it's made). False if it couldn't be read."""
        try:
            pic = P.load(path, self)
        except (tk.TclError, OSError, ValueError) as e:
            if not quiet:
                messagebox.showerror(tr("image.window_title"), tr("image.cant_open", e=e, kinds=_kinds()),
                                     parent=self)
            return False
        self.pic = pic
        if not self.locked and self.editing is None:  # (colours pasted / picked before are kept)
            self.locked = self.project_locks()
        self.made_for = (None, None)
        self.name.config(text=os.path.basename(path), foreground="#2a7")
        self.remake()  # (app.image_last: once it's made)
        self.after_made = then  # (set after remake: it drops one left from before)
        if then and not self.making and self.made and self.made[0] is pic:  # (made right away)
            self.after_made = None
            then()
        return True

    def remake(self):
        """Cells -> colours -> grid, each only when something it depends on changed. A big one is made in the
        background with a progress bar and Cancel (user); past BIG_CELLS cells it's asked first."""
        self.late = None
        self.stop_making()
        self.after_made = None  # (meant for the picture being made before)
        if not self.pic:
            self.redraw()
            return
        s = dict(self.s)
        rows, cols = P.grid_size(self.pic, s["keys"], s["steps"], s["view"])
        if rows * cols > BIG_CELLS and not self.big_ok(rows, cols):
            self.back_to_made()
            return
        cells_key = (s["keys"], s["steps"], s["view"], s["brightness"], s["contrast"], s["saturation"],
                     s["sharpen"])
        others, others_key = self.others_cells()
        pal_key = (cells_key, s["colours"], s["focus"], s["empty"], s["share"], others_key, tuple(sorted(
            (k, tuple(np.round(v, 6))) for k, v in self.locked.items())))
        job = Making(self.pic, s, (rows, cols), cells_key, pal_key,
                     (self.cl, self.al) if self.made_for[0] == cells_key else None,
                     self.pal if self.made_for[1] == pal_key else None, others,
                     [(k, v) for k, v in self.locked.items() if k < s["colours"]],
                     [(k, v) for k, v in self.start.items() if k < s["colours"]], self.editing)
        if rows * cols <= THREAD_CELLS:  # (quick: made right here)
            self.info.config(text=tr("image.working"))
            self.update_idletasks()
            job.run()
            self.finished(job)
            return
        self.making = job
        self.show_bar(True)
        threading.Thread(target=job.run, daemon=True).start()
        self.after(POLL_MS, self.poll, job)

    def big_ok(self, rows, cols):
        """More than BIG_CELLS cells: may it be made? (asked once for this picture and size; user's soft limit)"""
        key = (self.pic.sig, rows, cols)
        if key == self.big_yes or "image" in self.app.big_skip:
            return True
        from window import big_ask
        secs = rows * cols * self.app.image_rate
        go, skip = big_ask.dialog(self, tr("big_ask.title"), tr("image.big", rows=rows, cols=cols, cells=rows * cols,
                                                                 secs=max(1, round(secs))), False)
        if go:
            self.big_yes = key
            if skip:
                self.app.big_skip.add("image")
        return go

    def poll(self, job):
        """The picture made in the background: its progress shown, then the picture when it's done."""
        if job is not self.making or not self.winfo_exists():
            return
        if not job.done:
            self.bar.config(value=job.progress * 100)
            self.prog_text.config(text=tr("image.making", pct=int(job.progress * 100)))
            self.after(POLL_MS, self.poll, job)
            return
        self.making = None
        self.show_bar(False)
        self.finished(job)

    def finished(self, job):
        """A picture made: shown (an error: back to what was shown before)."""
        if job.cancel:
            return
        if job.error is not None:
            if not isinstance(job.error, MemoryError):
                raise job.error
            messagebox.showerror(tr("image.window_title"), tr("image.no_memory"), parent=self)
            self.back_to_made()
            return
        self.cl, self.al = job.cells
        self.pal, self.grid = job.pal, job.grid
        self.made_for = (job.cells_key, job.pal_key)
        self.made = (job.pic, job.s, job.editing)
        if job.size[0] * job.size[1] > THREAD_CELLS:  # (how long a cell takes on this PC, for the question)
            self.app.image_rate = job.seconds / (job.size[0] * job.size[1])
        self.app.image_last = (job.pic.path, dict(self.s))
        self.show_swatches()
        self.show_gives()
        self.redraw()
        then, self.after_made = self.after_made, None
        if then:
            then()

    def busy(self):
        """Still making the picture: says so (nothing can be placed / applied until it's done)."""
        if self.making:
            self.app.status.config(text=tr("image.still_making"))
        return bool(self.making)

    def stop_making(self):
        if self.making:
            self.making.cancel = True
            self.making = None
            self.show_bar(False)

    def cancel_making(self):
        """Cancel: the picture being made is dropped; the settings / picture go back to what's shown."""
        if self.making:
            self.stop_making()
            self.back_to_made()
            self.app.status.config(text=tr("image.cancelled"))

    def back_to_made(self):
        """The picture and settings back to the ones the preview was made from (a big one turned down, cancelled
        or out of memory); look settings stay as they are."""
        self.after_made = None
        if self.made is None:
            self.pic, self.grid = None, None
            self.name.config(text=tr("image.no_picture", kinds=_kinds()), foreground="#777")
        else:
            pic, s, editing = self.made
            self.pic = pic
            self.s = dict(s, **{k: self.s[k] for k in LOOK_ONLY + ("fmt", "by")})
            self.name.config(text=os.path.basename(pic.path), foreground="#2a7")
            if (editing or (0, 0, None))[2] != (self.editing or (0, 0, None))[2]:  # (another one was opened)
                self.editing = None
                self.apply_btn.pack_forget()
        self.show_settings()
        self.redraw()

    def show_bar(self, on):
        """The progress bar + Cancel in the middle of the preview (the picture dimmed behind it) while a picture is
        made in the background."""
        if on and not self.prog.winfo_manager():
            self.bar.config(value=0)
            self.prog_text.config(text=tr("image.making", pct=0))
            self.prog.place(in_=self.cv, relx=0.5, rely=0.5, anchor="center")
            self.redraw()
        elif not on and self.prog.winfo_manager():
            self.prog.place_forget()
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

    def fit(self, pic=None, view=None):
        """Where the picture goes in the canvas: (x, y, width, height), keyboard space left out."""
        cw, ch = self.view_size
        fall = (view or self.s["view"]) == "fall"
        aw, ah = (cw - 8, ch - KB - 8) if fall else (cw - KB - 8, ch - 8)
        pic = pic or self.pic
        asp = pic.aspect if pic else 16 / 9
        w = min(aw, ah * asp)
        h = w / asp
        w, h = max(8, int(w)), max(8, int(h))
        x = (cw - w - (0 if fall else KB)) // 2 + (0 if fall else KB)
        y = (ch - h - (KB if fall else 0)) // 2
        return x, y, w, h

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        if not self.pic or self.grid is None or self.made is None:
            if not self.making:  # (making: the progress box says so)
                cv.create_text(self.view_size[0] // 2, self.view_size[1] // 2, fill="#aaa",
                               text=tr("image.no_picture", kinds=_kinds()))
                self.info.config(text="")
            return
        pic, made, _ = self.made  # (what the shown grid was made from; the look settings: the window's own)
        view = made["view"]
        x, y, w, h = self.fit(pic, view)
        s = self.s
        if self.held:
            rgb = P.to_srgb(P.cells(pic, h, w)[0])
            self.what.config(text=tr("image.original_title"))
        else:
            rgb = look_picture(self.grid, P.to_srgb(self.pal), view, w, h, s["look"], self.outline_px(w, h, view),
                               s["shade"], s["join"])
            self.what.config(text=tr("image.preview_title"))
        if self.making:  # (dimmed behind the progress box)
            rgb = rgb * 0.35
        self.photo = _photo(self, rgb)
        cv.create_image(x, y, image=self.photo.photo, anchor="nw")
        self.draw_keys(x, y, w, h, view)
        rows, steps, keys = P.grid_notes(self.grid, view)
        notes = len(rows) if s["join"] else int((self.grid >= 0).sum())
        self.info.config(text=tr("image.info", keys=keys, steps=steps, notes=f"{notes:,}",
                                 used=len(np.unique(self.grid[self.grid >= 0])), colours=len(self.pal)))

    def outline_px(self, w, h, view=None):
        """The outline's width in the preview (w x h px): as wide next to a key as in the player's window (its
        outline px; its keys shown across its width / up its height), the preview's keys being wider / narrower."""
        s = self.s
        fall = (view or s["view"]) == "fall"
        keys = self.grid.shape[1 if fall else 0]
        player_key = (s["player_w"] if fall else s["player_h"]) / s["player_keys"]
        return s["outline"] * ((w if fall else h) / keys) / player_key

    def draw_keys(self, x, y, w, h, view):
        """A keyboard under (falling) / left of (piano roll) the picture, one key per grid key."""
        fall = view == "fall"
        cv, keys = self.cv, self.grid.shape[1 if fall else 0]
        if fall:
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

    def placed_shape(self, b0, k0):
        """The picture as a new shape from beat b0 and key k0 (its lowest) up, or None (no picture / nothing
        coloured)."""
        if not self.pic or self.grid is None:
            return None
        s = dict(self.s, step=self.step_beats())
        info = {"file": self.pic.path, "sig": self.pic.sig, "size": list(self.pic.size), "id": os.urandom(6).hex(),
                "set": dict(P.clean_settings(s), pal=[P.hex_of(c) for c in self.pal], locked=sorted(self.locked))}
        vel = self.app.defaults.get("vel0", 127)
        return P.picture_shape(self.grid, info, b0, k0, s["step"], vel)

    def step_beats(self):
        """One grid step in beats, never shorter than a tick (a shorter one lost notes at a low PPQ)."""
        return max(self.s["step"], 1 / self.app.ppq)

    def box_at(self, beat, key, snap=True):
        """The picture held by its middle at (beat, key) -> (start beat, lowest key, length in beats, keys): its
        start on the grid (snap), not before beat 0, every key inside the piano roll's key range (user: a picture
        placed half off the keys lost its bottom key)."""
        _, steps, keys = P.grid_notes(self.grid, self.s["view"])
        length = steps * self.step_beats()
        b0 = beat - length / 2
        sb = self.app.snap_beats()
        if snap and sb:
            b0 = round(b0 / sb) * sb
        k0 = max(0, min(self.app.keys - keys, round(key - (keys - 1) / 2)))
        return max(0.0, b0), k0, length, keys

    def place(self, beat, key, snap=False):
        """The picture placed held by its middle at (beat, key) (box_at)."""
        if self.busy():
            return False
        sh = self.placed_shape(*self.box_at(beat, key, snap)[:2]) if self.grid is not None else None
        if sh is None:
            self.app.status.config(text=tr("image.nothing_to_place"))
            return False
        self.app.add_picture(sh)
        return True

    def place_at_play_line(self):
        self.take_typed()
        if self.busy():
            return
        if self.grid is None:
            self.app.status.config(text=tr("image.nothing_to_place"))
            return
        roll = self.app.roll
        length = self.box_at(0, 0, snap=False)[2]
        self.place(self.app.playhead + length / 2, roll.y2p(roll.winfo_height() / 2))

    def drag_start(self, e):
        self.drag_mark = None
        self.take_typed()

    def roll_spot(self, e):
        """Where on the main piano roll the mouse is (beat, key, snap: Shift = off the grid), or None when it's not
        over it (nor over this window, which covers the piano roll there)."""
        from roll.roll_shared import SHIFT
        roll = self.app.roll
        x, y = e.x_root - roll.winfo_rootx(), e.y_root - roll.winfo_rooty()
        if self.grid is None or not (0 <= x < roll.winfo_width() and 0 <= y < roll.winfo_height() and x > roll.kb_w):
            return None
        wx, wy = e.x_root - self.winfo_rootx(), e.y_root - self.winfo_rooty()
        if 0 <= wx < self.winfo_width() and 0 <= wy < self.winfo_height():
            return None
        return roll.x2t(x), roll.y2p(y), not e.state & SHIFT

    def drag_move(self, e):
        roll = self.app.roll
        roll.delete("picdrag")
        spot = self.roll_spot(e)
        if spot is None or self.making:
            return
        b0, k0, length, keys = self.box_at(*spot)
        roll.create_rectangle(roll.t2x(b0), roll.p2y(k0 - 0.5 + keys), roll.t2x(b0 + length), roll.p2y(k0 - 0.5),
                              outline="#e02020", dash=(4, 3), width=2, tags="picdrag")

    def drag_end(self, e):
        self.app.roll.delete("picdrag")
        spot = self.roll_spot(e)
        if spot is not None:
            self.place(*spot)

    # ------------------------------------------------------------ a placed picture

    def edit(self, i, ask=True):
        """The placed picture i: its picture and settings here, "Apply to the placed picture" puts changes on it.
        ask: a missing / changed picture file asks what to do (not when another file is picked next anyway)."""
        sh = self.app.shapes[i]
        p = sh["picture"]
        p.setdefault("id", os.urandom(6).hex())  # (older pictures: found again by it, edited)
        self.editing = (sh, i, p["id"])
        self.s = P.clean_settings(p["set"])
        pal = p["set"].get("pal") or []
        self.locked = {k: P.lin_of(pal[k]) for k in p["set"].get("locked", []) if k < len(pal)}
        self.show_settings()
        if not self.apply_btn.winfo_manager():
            self.apply_btn.pack(side="right", padx=(0, 8))
        state = self.app.picture_state(p)
        if state == "ok":
            self.pic = self.app.picture_for(p)
            self.made_for = (None, None)
            self.name.config(text=os.path.basename(p["file"]), foreground="#2a7")
            self.remake()
            return
        self.pic, self.grid = None, None
        self.redraw()
        if state == "missing":  # (user: warn + a file picker)
            self.name.config(text=tr("image.missing", name=os.path.basename(p["file"])), foreground="#c60")
            if ask and messagebox.askyesno(tr("image.window_title"), tr("image.missing_ask", path=p["file"]),
                                           parent=self):
                self.ask_file()
            return
        if state == "unreadable":  # (e.g. a JPG placed where Pillow was installed: not "changed")
            self.name.config(text=tr("image.needs_pillow", name=os.path.basename(p["file"])), foreground="#c60")
            if ask:
                command = f'"{sys.executable.replace("pythonw", "python")}" -m pip install pillow'
                messagebox.showinfo(tr("image.window_title"),
                                    tr("image.needs_pillow_info", path=p["file"], command=command), parent=self)
            return
        # changed since it was placed (user: "Use the new version / Keep the current one")
        self.name.config(text=tr("image.changed", name=os.path.basename(p["file"])), foreground="#c60")
        if ask and messagebox.askyesno(tr("image.window_title"), tr("image.changed_ask", path=p["file"]), parent=self):
            self.app._pictures = {k: v for k, v in self.app._pictures.items() if k[0] != p["file"]}
            self.load(p["file"], then=self.apply)

    def edited(self):
        """The placed picture being changed, if it's still there (shape number), else None."""
        if not self.editing:
            return None
        sh, i, pid = self.editing
        shapes = self.app.shapes
        found = next((j for j, t in enumerate(shapes) if t is sh), None)
        if found is None:  # (undo / redo / Open made the shapes anew: the one with its id, the same place first)
            same = [j for j, t in enumerate(shapes) if t.get("picture", {}).get("id") == pid]
            found = i if i in same else (same[0] if same else None)
        if found is None:
            self.editing = None
            self.apply_btn.pack_forget()
            return None
        self.editing = (shapes[found], found, pid)
        return found

    def apply(self):
        """The window's picture and settings put on the placed picture, one undo step. Same picture shape and size
        settings: it keeps its box (turned, stretched...); else its box's middle stays."""
        self.take_typed()
        i = self.edited()
        if i is None or self.grid is None or self.busy():
            return
        app = self.app
        old = app.shapes[i]
        (b0, k0), (b1, k1), (b2, k2) = old["pts"]
        _, steps, keys = P.grid_notes(self.grid, self.s["view"])
        new = self.placed_shape((b1 + b2 - steps * self.step_beats()) / 2, round((k1 + k2 - keys + 1) / 2))
        if new is None:
            return
        w0, h0 = old["picture"]["size"]
        w1, h1 = new["picture"]["size"]
        s0 = old["picture"]["set"]
        keep = all(s0.get(k) == self.s[k] for k in ("keys", "steps", "view")) and abs(
            s0.get("step", 0) - new["picture"]["set"]["step"]) < 1e-9
        if abs(w0 / h0 - w1 / h1) > 0.01 * (w0 / h0):  # another shape of picture (user: ask only then)
            keep = not messagebox.askyesno(tr("image.window_title"), tr("image.shape_ask"), parent=self)
        app.push_undo(name=tr("image.apply_step"))
        new["vel0"], new["vel1"] = old.get("vel0", new["vel0"]), old.get("vel1", new["vel1"])
        if old.get("vel_env"):
            new["vel_env"] = old["vel_env"]
        new["picture"]["id"] = old["picture"].get("id", new["picture"]["id"])
        if keep:  # (its placed box: remake_pictures makes its notes for it, turned_notes when it's turned)
            new["pts"] = [list(pt) for pt in old["pts"]]
        app.shapes[i] = new
        self.editing = (new, i, new["picture"]["id"])
        missing = app.sync_pictures(new["picture"]["set"], i)
        app.shapes_changed()
        app.status.config(text=tr("image.applied", name=new["name"]) +
                          (tr("image.others_missing", n=missing) if missing else ""))

    # ------------------------------------------------------------ closing

    def on_configure(self, e):
        if e.widget is self:
            self.app.image_pos = self.geometry()

    def close(self):
        if self.late:
            self.after_cancel(self.late)
        self.stop_making()
        self.app.image_window = None
        self.app.roll.delete("picdrag")  # (closed while the handle was dragged)
        self.destroy()
        self.app.roll.focus_set()
