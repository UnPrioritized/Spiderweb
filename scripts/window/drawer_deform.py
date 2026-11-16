"""The drawer's Deform… (PLAN.md "Drawer deformation"): a small window bending the selected strokes (all but locked
ones when none are picked) with Turn, Twist, Bulge / Pinch and Ripple round a Centre mark, inside an area like
Stir's (notes/deform.py does the maths). Live on the board while it's open (each stroke shown as a fine bent line,
quick); Apply bends them for real (curves stay curves: deform.bent_stroke) as ONE undo step, Cancel / Esc / closing
puts everything back. Opens at the defaults every time (user). While it's open the drawer's other controls wait
(the board's mouse and keys only look: the view still scrolls and zooms)."""

import json
import math
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import fmt
from notes import deform
from notes import recolour as rc
from notes.custom import stroke_points
from window import look
from window.drawer_mirror import mirror_fns
from window.widgets import (Scrub, Tooltip, bad, good, name_index, name_list, read_number, remember_place,
                            set_names)

# key: (text key, slider low, slider high, box low, box high, default)
SLIDERS = {"turn": ("deform.turn", -360.0, 360.0, -rc.MOST_TURN, rc.MOST_TURN, 0.0),
           "twist": ("deform.twist", -5.0, 5.0, -rc.MOST_TIGHT, rc.MOST_TIGHT, 0.0),
           "bulge": ("deform.bulge", -200.0, 200.0, -rc.MOST_BULGE, rc.MOST_BULGE, 0.0),
           "ripple": ("deform.ripple", 0.0, 20.0, 0.0, rc.MOST_RIPPLE, 0.0),
           "ripple_len": ("deform.ripple_len", 1.0, 50.0, rc.LEAST_RIPPLE_LEN, rc.MOST_RIPPLE_LEN, 10.0),
           "size": ("deform.size", 1.0, 200.0, rc.LEAST_REACH, rc.MOST_REACH, 50.0)}
BOXES = {"stretch": (rc.LEAST_STRETCH, rc.MOST_STRETCH, 100.0), "stretch_up": (rc.LEAST_STRETCH, rc.MOST_STRETCH, 100.0),
         "tilt": (-360.0, 360.0, 0.0), "slant": (-100 * rc.MOST_SKEW, 100 * rc.MOST_SKEW, 0.0)}
# a number box's steps (Up / Down, wheel, a drag): (step, Shift step, Ctrl step)
STEPS = {"turn": (1, 15, 0.1), "twist": (0.05, 0.5, 0.01), "bulge": (1, 10, 0.1), "ripple": (0.1, 1, 0.01),
         "ripple_len": (0.5, 5, 0.1), "size": (1, 10, 0.1), "stretch": (1, 10, 0.1), "stretch_up": (1, 10, 0.1),
         "tilt": (1, 15, 0.1), "slant": (1, 10, 0.1)}
STICKY = ("turn", "twist", "bulge")  # (a slider's knob dragged near 0 stays there, user 2026-11-16)
STICK_PX = 6  # (that near, in pixels along the slider)
AREAS = ("whole", "circle", "square", "diamond")
REST_MS = 300  # (the sliders resting this long: area colours worked out again)
BLOCK = "DrawerDeformBlock"  # (the board's bind tag while the window is open: its own clicks / keys wait)
BLOCKED = ("<Double-Button-1>", "<ButtonRelease-3>", "<Double-Button-3>", "<Key>", "<KeyRelease>")
GRAB = 8  # px: how near the Centre mark / the area's line the mouse must be to grab it
BOX_LEAST = 40  # px: an area box with a side shorter than this on screen isn't shown (its line sizes it then)
RING_POINTS = 72  # (a round area's line: pieces)
TURN_STEP = 15  # degrees: the area box turned in steps of this (Shift = free), like a shape's box
CORNERS = ((0, 0), (1, 0), (1, 1), (0, 1))  # (the box's corners going round, in its own (across, up))


class DrawerDeform:
    """The Drawer's side of Deform…"""
    deform_win = None
    areas_wait = False  # (the area colours not worked out again until the sliders rest: drawer_areas.area_image)

    def deform_targets(self):
        """What Deform bends: the selected strokes the board can change, else every stroke but locked ones (hidden
        ones too, user)."""
        if self.chosen():
            return self.movable()
        return [i for i in range(len(self.strokes)) if not self.is_locked(i)]

    def open_deform(self):
        win = self.deform_win
        if win is not None and win.winfo_exists():
            return win.lift()
        if self.draft or self.holding() or self.lasso_holding() or self.knife or self.lasso:
            return messagebox.showinfo(tr("drawer.spiderweb"), tr("deform.busy"), parent=self)
        if not self.deform_targets():
            return messagebox.showinfo(tr("drawer.spiderweb"),
                                       tr("deform.all_locked" if self.strokes else "deform.nothing"), parent=self)
        self.deform_win = DeformWindow(self)

    def deforming(self):
        return self.deform_win is not None


class DeformWindow(tk.Toplevel):
    def __init__(self, dr):
        super().__init__(dr)
        self.dr = dr
        s = self.s = dr.scale
        self.title(tr("deform.title"))
        self.transient(dr)
        self.resizable(False, False)
        remember_place(self, "drawer_deform")
        # the drawing as it was: everything is worked out from it again at each change
        self.base_snap = dr.snap()
        self.base_strokes, self.base_areas = json.loads(self.base_snap)
        self.base_sel = dr.sel_state()
        self.base_dirty = dr.dirty
        self.idx = dr.deform_targets()
        self.whole = not dr.chosen()
        mirror = dr.mirror_mode() != "off"
        self.leads = dr.one_per_link(self.idx) if mirror else list(self.idx)
        table = dr.mirror_table()
        self.remade = {k for i in self.leads for k in dr.partners(i, table)} - set(self.leads)
        self.bend_idx = [i for i in self.idx if i not in self.remade]
        moved = sorted(set(self.bend_idx) | self.remade)
        self.going = [True] * len(self.base_areas) if self.whole else dr.carried_areas(moved)
        self.mirror_ways = mirror_fns(dr.mirror_mode())
        pts = np.asarray([p for i in self.idx for p in stroke_points(self.base_strokes[i])], float).reshape(-1, 2)
        mid = ((pts.min(0) + pts.max(0)) / 2).tolist() if len(pts) else [0.5, 0.5]
        self.mid = list(mid)  # (where the Centre mark starts: right-click puts it back)
        self.centre, self.area_middle = list(mid), list(mid)
        self.grab = None  # (what the mouse holds on the board: the Centre mark / the area / a box handle)
        self.shape = None  # (a library drawing picked as the area: recolour's area shape)
        self.vars, self.scales, self.boxes = {}, {}, {}
        self.holding = None  # (the slider whose knob the mouse holds)
        self._job = self._rest = None
        self._curves = None  # (settings, {stroke: bent curve}): rested() works them out, Apply reuses them
        self._touched = None  # (points stuck on other bent strokes: deform.touches, once)
        self.applied = False
        self._build()
        self.block(True)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<F1>", lambda e: (dr.app.open_help("drawer_deform"), "break")[1])
        self.after_idle(lambda: self.winfo_exists() and dr.app.tips.show("drawer_deform", parent=self, wait=True))
        self.after_idle(self.focus_first)

    # ------------------------------------------------------------ building

    def _build(self):
        s = self.s
        top = ttk.Frame(self, padding=(int(10 * s), int(8 * s)))
        top.pack(fill="both", expand=True)
        n = len(self.idx)
        what = trn("deform.what_all" if self.whole else "deform.what_some", n, n=n)
        if self.whole and any(self.dr.is_locked(i) for i in range(len(self.base_strokes))):
            what += " " + tr("deform.what_locked")
        if self.remade:
            what += " " + tr("deform.what_mirror")
        wrap = int(330 * s)  # (fixed: the window is as big as what's in it, wrap_to_room would loop)
        self.what = ttk.Label(top, text=what, justify="left", wraplength=wrap)
        bends = ttk.LabelFrame(top, text=tr("deform.bends"), padding=(int(6 * s), int(2 * s), int(6 * s), int(4 * s)))
        bends.pack(fill="x")
        for row, key in enumerate(("turn", "twist", "bulge", "ripple", "ripple_len")):
            self.slider_row(bends, row, key)
        ttk.Label(bends, text=tr("deform.ripple_kind")).grid(row=5, column=0, sticky="w", pady=(2, 0))
        names = [tr("deform.ripple_" + k) for k in deform.RIPPLE_KINDS]
        self.kind = ttk.Combobox(bends, values=names, state="readonly", width=max(map(len, names)) + 1)
        self.kind.current(0)
        self.kind.grid(row=5, column=1, columnspan=2, sticky="w", padx=(int(6 * s), 0), pady=(2, 0))
        self.kind.bind("<<ComboboxSelected>>", lambda e: self.changed())
        Tooltip(self.kind, tr("deform.ripple_kind_tip"))

        area = ttk.LabelFrame(top, text=tr("deform.area_box"), padding=(int(6 * s), int(2 * s), int(6 * s), int(4 * s)))
        area.pack(fill="x", pady=(int(8 * s), 0))
        ttk.Label(area, text=tr("deform.area")).grid(row=0, column=0, sticky="w")
        self.area = name_list(ttk.Combobox(area, state="readonly", width=16))
        self.area_names()
        self.area.current(0)
        self.area.grid(row=0, column=1, columnspan=2, sticky="w", padx=(int(6 * s), 0))
        self.area.bind("<<ComboboxSelected>>", lambda e: self.area_picked(), add="+")
        Tooltip(self.area, tr("deform.area_tip"))
        self.area_parts = [self.slider_row(area, 1, "size")]
        r = ttk.Frame(area)
        r.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        lab = ttk.Label(r, text=tr("deform.stretch"))
        lab.pack(side="left")
        Tooltip(lab, tr("deform.stretch_tip"))
        for key, word in (("stretch", "deform.stretch_across"), ("stretch_up", "deform.stretch_up")):
            w = ttk.Label(r, text=tr(word))
            w.pack(side="left", padx=(int(8 * s), int(3 * s)))
            self.area_parts.append(self.number_box(r, key, label=w))
        r = ttk.Frame(area)
        r.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for key, label in (("tilt", "deform.angle"), ("slant", "deform.slant")):
            lab = ttk.Label(r, text=tr(label))
            lab.pack(side="left", padx=(0 if key == "tilt" else int(12 * s), int(3 * s)))
            Tooltip(lab, tr(label + "_tip"))
            self.area_parts.append(self.number_box(r, key, label=lab))
        self.hard = tk.BooleanVar(value=False)
        b = ttk.Checkbutton(area, text=tr("deform.hard"), variable=self.hard, command=self.changed)
        b.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        Tooltip(b, tr("deform.hard_tip"))
        self.area_parts.append(b)
        self.area_line = ttk.Label(area, text="", foreground=look.WARN_DARK, justify="left", wraplength=wrap)
        self.area_line.grid(row=5, column=0, columnspan=3, sticky="we")
        self.show_area_parts()

        self.what.pack(fill="x", pady=(int(8 * s), 0))
        ttk.Label(top, text=tr("deform.board_hint"), foreground=look.HINT, justify="left",
                  wraplength=wrap).pack(fill="x", pady=(int(4 * s), 0))
        self.heavy = ttk.Label(top, text="", foreground=look.WARN_DARK, justify="left", wraplength=wrap)
        self.heavy.pack(fill="x")
        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(int(8 * s), 0))
        ttk.Button(btns, text=tr("deform.cancel"), command=self.cancel).pack(side="right")
        ttk.Button(btns, text=tr("deform.apply"), command=self.apply).pack(side="right", padx=(0, int(6 * s)))

    def slider_row(self, parent, row, key):
        """Label, slider, number box. The slider covers the usual range, the box takes the whole range."""
        text, lo, hi, blo, bhi, plain = SLIDERS[key]
        lab = ttk.Label(parent, text=tr(text))
        lab.grid(row=row, column=0, sticky="w")
        Tooltip(lab, tr(text + "_tip"))
        var = tk.DoubleVar(value=plain)
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, length=int(200 * self.s),
                          command=lambda v, k=key: self.slid(k, float(v)))
        scale.grid(row=row, column=1, padx=(int(6 * self.s), int(6 * self.s)), pady=1)
        # (user 2026-11-16: a double-click reset fired while clicking quickly to step by 1: middle-click instead,
        # like Stir's knobs; the knob held = a drag, sticking at 0)
        scale.bind("<ButtonPress-2>", lambda e, k=key: (self.put(k, SLIDERS[k][5]), "break")[1])
        scale.bind("<ButtonPress-1>", lambda e, k=key, sc=scale: setattr(
            self, "holding", k if "slider" in str(sc.identify(e.x, e.y)) else None), add="+")
        scale.bind("<ButtonRelease-1>", lambda e: setattr(self, "holding", None), add="+")
        self.scales[key] = (scale, var)
        box = self.number_box(parent, key, grid=(row, 2), label=lab)
        return box

    def number_box(self, parent, key, grid=None, label=None):
        """A number box like the program's others (user 2026-11-16): typing, Up / Down, the wheel once clicked,
        dragging its label or the box itself sideways (Shift = big steps, Ctrl = fine; widgets.Scrub)."""
        lo, hi, plain = (SLIDERS[key][3:] if key in SLIDERS else BOXES[key])
        var = self.vars[key] = tk.StringVar(value=fmt(plain))
        e = ttk.Entry(parent, textvariable=var, width=7, justify="center")
        if grid:
            e.grid(row=grid[0], column=grid[1], sticky="w")
        else:
            e.pack(side="left")
        good(e)
        e.bind("<Return>", lambda ev, k=key: self.typed(k))
        e.bind("<FocusOut>", lambda ev, k=key: self.typed(k))
        self.boxes[key] = e
        e.value = plain
        Scrub(self.dr.app, [(e, var, lambda k=key: self.typed(k))], STEPS[key], lo, hi, label=label, drag_box=True,
              no_undo=True)
        return e

    def area_names(self):
        from window.drawer_library import library_names
        self.library = library_names()
        set_names(self.area, [tr("deform.area_" + k) for k in AREAS], self.library)

    def focus_first(self):
        if self.winfo_exists():
            self.boxes["turn"].focus_set()

    # ------------------------------------------------------------ values

    def value(self, key):
        return self.boxes[key].value

    def put(self, key, v):
        """A number set (slider / box / double-click): shown in both, the board updated."""
        lo, hi, _ = SLIDERS[key][3:] if key in SLIDERS else BOXES[key]
        v = min(max(float(v), lo), hi)
        e = self.boxes[key]
        e.value = v
        self.vars[key].set(fmt(round(v, 3)))
        good(e)
        if key in self.scales:
            self.scales[key][1].set(v)
        self.changed()

    def slid(self, key, v):
        if self.holding == key and key in STICKY:  # (dragged near 0: stays at 0, like a centre detent)
            _, lo, hi = SLIDERS[key][:3]
            if abs(v) <= (hi - lo) * STICK_PX * self.s / max(self.scales[key][0].winfo_width(), 1):
                v = 0.0
                self.scales[key][1].set(0.0)
        if abs(v - self.value(key)) > 1e-12:
            self.put(key, round(v, 3))

    def typed(self, key):
        lo, hi, _ = SLIDERS[key][3:] if key in SLIDERS else BOXES[key]
        e = self.boxes[key]
        v = read_number(self.vars[key].get(), lo, hi)
        if v is None:
            return bad(e)
        if v != e.value:
            self.put(key, v)
        else:
            good(e)

    def area_kind(self):
        k = name_index(self.area)
        if 0 <= k < len(AREAS):
            return AREAS[k]
        return rc.DRAWN if k >= len(AREAS) and self.shape else "whole"

    def area_picked(self):
        k = name_index(self.area)
        self.area_line.config(text="")
        if k >= len(AREAS):
            from window.stir_window import library_area
            name = self.library[k - len(AREAS)]
            shape = library_area(self.dr.app, name)
            if not shape:
                self.area_line.config(text=tr("deform.no_inside", name=name))
                self.area.current(0)
            self.shape = shape
        self.show_area_parts()
        self.changed()

    def show_area_parts(self):
        on = self.area_kind() != "whole"
        for w in self.area_parts:
            w.state(["!disabled"] if on else ["disabled"])
        scale = self.scales["size"][0]
        scale.state(["!disabled"] if on else ["disabled"])

    def settings(self):
        """The window's settings for notes/deform.py (cleaned)."""
        d = {k: self.value(k) for k in ("turn", "twist", "bulge", "ripple", "ripple_len")}
        d["ripple_kind"] = deform.RIPPLE_KINDS[max(self.kind.current(), 0)]
        d["centre"] = self.centre
        kind = self.area_kind()
        if kind != "whole":
            d["area"] = {"kind": kind, "shape": self.shape, "middle": self.area_middle, "size": self.value("size"),
                         "stretch": self.value("stretch"), "stretch_up": self.value("stretch_up"),
                         "tilt": self.value("tilt"), "skew": self.value("slant") / 100, "hard": self.hard.get()}
        return deform.clean(d)

    # ------------------------------------------------------------ the board

    def changed(self):
        """Shown on the board once things settle (a held slider sends many moves)."""
        if self._job is None:
            self._job = self.after_idle(self.show)

    def show(self, final=False):
        """The drawing bent by the window's settings, from the drawing as it was. final: bent for real
        (deform.bent_stroke), else quickly as fine lines (deform.preview_points)."""
        self._job = None
        dr, d = self.dr, self.settings()
        strokes = list(self.base_strokes)
        if deform.moves(d):
            done = self.bent_curves(d) if final else {}
            new = {}
            for i in self.bend_idx:
                st = self.base_strokes[i]
                if i in done:
                    new[i] = done[i]
                elif final:
                    new[i] = deform.bent_stroke(st, d)
                else:
                    new[i] = {k: st[k] for k in deform.PLAIN_KEEP if k in st}
                    new[i].update(kind="poly", pts=deform.preview_points(st, d).tolist())
            # (points stuck on lines put back exactly on them, live too: no new red dots / closing lines, user)
            new = deform.keep_touching(new, self.touched(), d)
            for i, st in new.items():
                strokes[i] = st
        dr.strokes = strokes
        dr.mirror_from(self.leads)
        dr.areas = self.moved_areas(d)
        if final:  # (Apply: changed() draws it once)
            return
        if self._rest is not None:
            self.after_cancel(self._rest)
        self._rest = self.after(REST_MS, self.rested)
        dr.areas_wait = True
        dr.redraw()

    def rested(self):
        """The sliders rested: the area colours worked out for the bent drawing, and the line saying which curves
        Apply turns into fine lines."""
        self._rest = None
        dr = self.dr
        dr.areas_wait = False
        d = self.settings()
        done = self.bent_curves(d) if deform.moves(d) else {}
        n = sum(1 for i, st in done.items() if st["kind"] != "curve")
        self.heavy.config(text=trn("deform.heavy", n, n=n) if n else "")
        dr.redraw()

    def touched(self):
        """deform.touches of the strokes bent (worked out once: the drawing as it was doesn't change)."""
        if self._touched is None:
            self._touched = deform.touches(self.base_strokes, self.bend_idx)
        return self._touched

    def bent_curves(self, d):
        """The curves Apply bends, bent for real (deform.bent_stroke), kept for these settings."""
        key = json.dumps(d)
        if self._curves is None or self._curves[0] != key:
            got = {i: deform.bent_stroke(self.base_strokes[i], d) for i in self.bend_idx
                   if self.base_strokes[i]["kind"] == "curve"}
            self._curves = (key, got)
        return self._curves[1]

    def moved_areas(self, d):
        """The coloured areas' spots (those the bent strokes carry) bent too; on Mirror's grey side by the mirrored
        bend (the copies bend as mirror images)."""
        if not deform.moves(d):
            return json.loads(json.dumps(self.base_areas))
        out = []
        for (u, v, c), go in zip(self.base_areas, self.going):
            if go:
                fn = next((f for f in self.mirror_ways if not self.dr.mirror_side(*f(u, v))), None) \
                    if self.mirror_ways and self.dr.mirror_side(u, v) else None
                p = fn(u, v) if fn else (u, v)
                x, y = deform.bend([p[0]], [p[1]], d)
                p = (float(x[0]), float(y[0]))
                u, v = fn(*p) if fn else p
            out.append([round(u, 5), round(v, 5), c])
        return out

    def block(self, on):
        """While open: the drawer's bars and side panel wait (tk busy), the board's clicks and keys go nowhere
        (its own bind tag first), the view still scrolls / zooms."""
        dr = self.dr
        c = dr.canvas
        for w in dr.winfo_children():
            if w is c or isinstance(w, tk.Toplevel):
                continue
            try:
                if on:
                    self.tk.call("tk", "busy", "hold", w, "-cursor", "arrow")
                else:
                    self.tk.call("tk", "busy", "forget", w)
            except tk.TclError:
                pass
        tags = [t for t in c.bindtags() if t != BLOCK]
        if on:
            for seq in BLOCKED:
                c.bind_class(BLOCK, seq, lambda e: "break")
            # (the board's own left / right clicks wait; the Centre mark and the area take them: stage D)
            for seq, fn in (("<ButtonPress-1>", self.board_press), ("<B1-Motion>", self.board_drag),
                            ("<ButtonRelease-1>", self.board_release), ("<Motion>", self.board_hover),
                            ("<ButtonPress-3>", self.board_right)):
                c.bind_class(BLOCK, seq, lambda e, fn=fn: (fn(e), "break")[1])
            # (middle drag still moves the view: "<Motion>" above would catch it; a middle click without a drag
            # adds no curve point while bending)
            c.bind_class(BLOCK, "<B2-Motion>", lambda e: None)
            c.bind_class(BLOCK, "<ButtonRelease-2>", lambda e: (setattr(dr, "_pan", None), "break")[1])
            tags.insert(0, BLOCK)
        c.bindtags(tuple(tags))

    # ------------------------------------------------------------ Centre mark + area on the board (stage D)

    def area_now(self):
        """The area as set now (cleaned), or None (Whole drawing)."""
        return self.settings()["area"]

    def area_rings(self, a):
        """The area's outline(s) on the board, each an (n, 2) array of (u, v), closed (a drawn area: one per loop)."""
        st = deform.as_stir(a)
        out = []
        for dx, dy in rc.area_outlines(st, RING_POINTS):
            gx, gy = rc.area_global(dx, dy, st, deform.ONE)
            out.append(np.column_stack((a["middle"][0] + gx, a["middle"][1] + gy)))
        return out

    def area_corners(self, a):
        """The area's box: its four corners (u, v) going round from its own (-, -)."""
        dx, dy = rc.area_corners(deform.as_stir(a), deform.ONE)
        m = a["middle"]
        return [(m[0] + float(dx[i]), m[1] + float(dy[i])) for i in range(4)]

    def screen_box(self, a):
        """The area box's corners on screen, or None when a side is too short there (BOX_LEAST)."""
        c = [self.dr.to_screen(*p) for p in self.area_corners(a)]
        if min(math.dist(c[0], c[1]), math.dist(c[0], c[3])) < BOX_LEAST * self.s:
            return None
        return c

    def draw_marks(self, board):
        """Drawn by the drawer's redraw (through its board): the area's dashed line + its box (green, like the
        piano roll's Stir area box), then the Centre mark (a green ring with a dot, like Stir's Centre point)."""
        dr, s = self.dr, self.s
        line = max(1, round(2 * s))
        band = line + 2 * max(1, round(s))
        a = self.area_now()
        if a is not None:
            for ring in self.area_rings(a):
                coords = dr.screen_points(ring.tolist())
                board.create_line(*coords, fill=look.ROLL_BG, width=band)
                board.create_line(*coords, fill=look.STIR_RING, width=line, dash=(6, 4))
            c = self.screen_box(a)
            if c:
                coords = [v for p in c + c[:1] for v in p]
                board.create_line(*coords, fill=look.STIR_BOX_BAND, width=band)
                board.create_line(*coords, fill=look.STIR_BOX, width=line)
                for (hx, hy), hr in [(p, 6 * s) for p in c] + [(p, 5 * s) for p in self.side_middles(c)]:
                    board.create_oval(hx - hr, hy - hr, hx + hr, hy + hr, outline=look.STIR_BOX_BAND,
                                      fill=look.STIR_BOX_FILL, width=line + 1)
        x, y = dr.to_screen(*self.centre)
        r, q = 6 * s, 2.4 * s
        board.create_oval(x - r, y - r, x + r, y + r, outline=look.PATH_LINE, fill=look.ROLL_BG, width=line)
        board.create_oval(x - q, y - q, x + q, y + q, outline="", fill=look.PATH_LINE)

    @staticmethod
    def side_middles(c):
        return [((c[i][0] + c[(i + 1) % 4][0]) / 2, (c[i][1] + c[(i + 1) % 4][1]) / 2) for i in range(4)]

    def hit(self, e):
        """What the mouse is over: ("centre",) / ("corner", k) / ("side", k) / ("turn", k) / ("skew", k) (just
        outside a corner / a side's middle, like a shape's box) / ("size",) (the line of an area too small for a
        box) / ("move",) (inside the area or on its line) / None."""
        dr, s = self.dr, self.s
        x, y = dr.to_screen(*self.centre)
        near_centre = math.hypot(x - e.x, y - e.y)
        a = self.area_now()
        if a is None:
            return ("centre",) if near_centre <= GRAB * s else None
        c = self.screen_box(a)
        line = min(self.line_near(dr.screen_points(r.tolist()), e.x, e.y) for r in self.area_rings(a))
        if near_centre <= GRAB * s and near_centre <= line:  # (a small area's line by the mark: the nearer wins)
            return ("centre",)
        if c:
            dist = lambda p: math.hypot(p[0] - e.x, p[1] - e.y)
            mids = self.side_middles(c)
            k = min(range(4), key=lambda i: dist(c[i]))
            m = min(range(4), key=lambda i: dist(mids[i]))
            if dist(c[k]) <= 9 * s:
                return ("corner", k)
            if dist(mids[m]) <= 8 * s:
                return ("side", m)
            if not self.inside(c, e.x, e.y):
                if dist(mids[m]) <= 20 * s and dist(mids[m]) < dist(c[k]):
                    return ("skew", m)
                if dist(c[k]) <= 22 * s:
                    return ("turn", k)
        elif line <= GRAB * s:
            return ("size",)
        if near_centre <= GRAB * s:
            return ("centre",)
        u, v = dr.from_screen(e.x, e.y)
        if line <= GRAB * s or any(self.inside(r, u, v) for r in self.area_rings(a)):
            return ("move",)
        return None

    @staticmethod
    def line_near(coords, x, y):
        """How near (px) the point x, y is to the polyline coords [x0, y0, x1, y1, ...]."""
        p = np.asarray(coords, np.float64).reshape(-1, 2)
        if len(p) < 2:
            return math.inf
        a, d = p[:-1], p[1:] - p[:-1]
        t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / np.maximum((d * d).sum(1), 1e-12), 0, 1)
        return float(np.hypot(a[:, 0] + t * d[:, 0] - x, a[:, 1] + t * d[:, 1] - y).min())

    @staticmethod
    def inside(poly, x, y):
        """Whether x, y is inside the closed line poly (odd crossings)."""
        p = np.asarray(poly, np.float64).reshape(-1, 2)
        a, b = p, np.roll(p, -1, axis=0)
        cross = (a[:, 1] > y) != (b[:, 1] > y)
        if not cross.any():
            return False
        a, b = a[cross], b[cross]
        xs = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
        return bool((xs > x).sum() % 2)

    def board_hover(self, e):
        h = self.hit(e)
        cursor = "crosshair"
        if h and h[0] in ("side", "skew"):
            cursor = "sb_h_double_arrow" if (h[1] % 2) == (h[0] == "side") else "sb_v_double_arrow"
        elif h:
            cursor = {"centre": "fleur", "move": "fleur", "turn": "exchange"}.get(h[0], "sizing")
        if str(self.dr.canvas.cget("cursor")) != cursor:
            self.dr.canvas.config(cursor=cursor)

    def board_press(self, e):
        h = self.hit(e)
        self.grab = None
        if h is None:
            return
        a = self.area_now()
        at = self.dr.from_screen(e.x, e.y)
        if h[0] == "centre":
            self.grab = (h, (at[0] - self.centre[0], at[1] - self.centre[1]))
        elif h[0] == "move":
            self.grab = (h, (at[0] - self.area_middle[0], at[1] - self.area_middle[1]))
        else:
            self.grab = (h, at, a, self.area_corners(a))

    def board_drag(self, e):
        if not self.grab:
            return
        h = self.grab[0]
        u, v = self.dr.from_screen(e.x, e.y)
        if h[0] in ("centre", "move"):
            off = self.grab[1]
            spot = [round(u - off[0], 5), round(v - off[1], 5)]
            if h[0] == "centre":
                self.centre = spot
            else:
                self.area_middle = spot
            return self.changed()
        at, a, c = self.grab[1:]
        m = a["middle"]
        if h[0] == "size":  # (the line of a small area: Size grows with the mouse's distance from its middle)
            d0 = math.hypot(at[0] - m[0], at[1] - m[1])
            if d0 > 1e-9:
                self.put("size", round(a["size"] * math.hypot(u - m[0], v - m[1]) / d0, 3))
            return
        if h[0] == "turn":
            turn = math.degrees(math.atan2(v - m[1], u - m[0]) - math.atan2(at[1] - m[1], at[0] - m[0]))
            if not e.state & 0x0001:  # (Shift = free, else in steps)
                turn = round(turn / TURN_STEP) * TURN_STEP
            return self.put("tilt", self.angle(a["tilt"] + turn))
        p0, pu, pv = (np.asarray(p, np.float64) for p in (c[0], c[1], c[3]))
        if h[0] == "skew":  # (the side's two corners slide along it with the mouse)
            k = h[1]
            j = (k + 1) % 4
            along = np.asarray(c[j], np.float64) - np.asarray(c[k], np.float64)
            along /= max(float(np.hypot(*along)), 1e-12)
            shift = along * float(np.dot((u - at[0], v - at[1]), along))
            new = [np.asarray(p, np.float64) + (shift if i in (k, j) else 0) for i, p in enumerate(c)]
            return self.box_to_area(a, new[0], new[1], new[3], "size")
        # a corner / a side: the opposite one stays, the held one follows the mouse (in the box's own across / up)
        A = np.column_stack((pu - p0, pv - p0))
        try:
            fa, fb = np.linalg.solve(A, np.array([u, v]) - p0)
        except np.linalg.LinAlgError:
            return
        k = h[1]
        if h[0] == "corner":
            cu, cv = CORNERS[k]
        else:  # (side k runs from corner k to k + 1: it moves one way only)
            cu, cv = ((None, 0), (1, None), (None, 1), (0, None))[k]
        ctrl = h[0] == "corner" and e.state & 0x0004
        if ctrl:  # (Ctrl on a corner: both ways by the same share = proportions kept, Size changes)
            f = ((fa - (1 - cu)) / (2 * cu - 1) + (fb - (1 - cv)) / (2 * cv - 1)) / 2
            fa, fb = (1 - cu) + f * (2 * cu - 1), (1 - cv) + f * (2 * cv - 1)
        lo = 0.02  # (never flat or inside out)
        ua = (0.0, 1.0) if cu is None else ((0.0, max(fa, lo)) if cu else (min(fa, 1 - lo), 1.0))
        va = (0.0, 1.0) if cv is None else ((0.0, max(fb, lo)) if cv else (min(fb, 1 - lo), 1.0))
        P = lambda s, t: p0 + s * (pu - p0) + t * (pv - p0)
        self.box_to_area(a, P(ua[0], va[0]), P(ua[1], va[0]), P(ua[0], va[1]), "stretch" if ctrl else "size")

    def box_to_area(self, a, p0, pu, pv, keep):
        """A box dragged on the board (its corners (-, -), (+, -), (-, +)) -> the area's middle, Size, Stretch,
        Angle and Slant (Stir's area_from_box); a box past a limit stays as it was last."""
        st = deform.as_stir(a)
        got = rc.area_from_box(st, *(np.asarray(p) * 100 for p in (p0, pu, pv)), deform.ONE, keep=keep)
        if got is None:
            return
        ok = (rc.LEAST_REACH <= got["reach"] <= rc.MOST_REACH and abs(got["skew"]) <= rc.MOST_SKEW
              and all(rc.LEAST_STRETCH <= got[k] <= rc.MOST_STRETCH for k in ("stretch", "stretch_up")))
        if not ok:
            return
        self.area_middle = [round(v / 100, 5) for v in got["middle"]]
        if keep == "stretch":
            self.put("size", round(got["reach"], 3))
        for key, v in (("stretch", got["stretch"]), ("stretch_up", got["stretch_up"]),
                       ("tilt", self.angle(got["tilt"])), ("slant", 100 * got["skew"])):
            self.put(key, round(v, 3))

    @staticmethod
    def angle(v):
        """An angle kept within -180..180."""
        return (v + 180) % 360 - 180

    def board_release(self, e):
        self.grab = None

    def board_right(self, e):
        """A right-click on the Centre mark: back to where it started (the strokes' middle)."""
        if self.hit(e) == ("centre",):
            self.centre = list(self.mid)
            self.changed()

    # ------------------------------------------------------------ done

    def apply(self):
        for key in self.boxes:  # (a number typed without Enter counts)
            if str(self.focus_get()) == str(self.boxes[key]):
                self.typed(key)
        d = self.settings()
        if not deform.moves(d):
            return self.cancel()
        self.show(final=True)
        dr = self.dr
        dr.push_undo(before=self.base_snap)
        self.applied = True
        self.finish()
        dr.changed(settle=False)

    def cancel(self):
        dr = self.dr
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        if not self.applied:
            dr.load_snap(self.base_snap)
            dr.sel, picks, dr.boxes = self.base_sel
            dr.picks = set(picks)
            dr.dirty = self.base_dirty
        self.finish()
        if dr.winfo_exists():
            dr.redraw()

    def finish(self):
        for job in (self._job, self._rest):
            if job is not None:
                self.after_cancel(job)
        self._job = self._rest = None
        dr = self.dr
        dr.areas_wait = False
        if dr.winfo_exists():
            self.block(False)
            dr.canvas.config(cursor="crosshair")
            dr.canvas.focus_set()
        dr.deform_win = None
        self.destroy()
