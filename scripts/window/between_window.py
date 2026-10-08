"""Add between (notes/between.py): the window (right-click two open lines > Add between…, or a group's shape >
Edit add between… / double-click) and the main window's part (BetweenGroups, mixed into App): the group as one row
of the Shapes list, menu items, unlinking.

The window: how many steps, Reverse (the last shape's points paired the other way round), each shape its own colour
(taking turns over n colours; needs Multi channel), and a graph of how the steps change from the first shape to the
last. Changes show on the piano roll at once; OK keeps them as one undo step, Cancel / Esc puts everything back.
Ctrl+Z / Ctrl+Y step through the changes made in the window."""

import json
import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc
from notes import between
from notes.engine import cached_path
from window import look
from window.widgets import LocalUndo, Scrub, Tooltip, remember_place


def _power(k):
    return [[i / 8, (i / 8) ** k] for i in range(9)]


def _s_curve(k):
    return [[i / 8, (i / 8) ** k / ((i / 8) ** k + (1 - i / 8) ** k)] for i in range(9)]


PRESETS = [("between.even", between.STRAIGHT), ("between.near_first", _power(2)),
           ("between.near_last", _power(0.5)), ("between.near_ends", _s_curve(0.5)),
           ("between.near_middle", _s_curve(2))]
U_SNAP, Y_SNAP = 1 / 40, 1 / 20  # dragging moves points in these steps (Shift = free)
PREVIEW_POINTS = 100000  # the preview's steps drawn with at most about this many points in all
PAD_STICK = 8  # the push pad's middle pulls the stick in within this many px (Shift = not)


class BetweenWindow(tk.Toplevel):
    def __init__(self, app, gid, before, name, opened_on=None):
        """gid: the group (made already); before: the shapes as JSON before the window (Cancel puts them back, OK
        makes one undo step from them, called name); opened_on: the shape it was opened from (the pad starts on it
        when it's the first / last shape or a key)."""
        super().__init__(app)
        self.app, self.gid, self.before, self.name = app, gid, before, name
        self.title(tr("between.title"))
        self.transient(app)
        self.resizable(False, False)
        s = self.s = app.scale
        self.w, self.h, self.ph = int(380 * s), int(140 * s), int(200 * s)
        self.ml, self.mr, self.mt, self.mb = int(40 * s), int(12 * s), int(10 * s), int(22 * s)
        self.sel_before = (set(app.sels), app.sel)
        self.set = between.settings_of(app.shapes, gid)
        self.kept_turns = self.set["colours"] or between.COLOURS_MOST
        self.closed = False
        self.drag, self.hover = None, None
        self.pv_drag, self.pv_view, self.pv_hover = None, None, None  # (a point of the first / last shape held)
        self.colours_touched = False  # (the Multi channel note only shows once the colours are changed: user)
        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        # (two rows of two: the preview + the push pad, the graph + the settings; titles over both boxes, user)
        top = ttk.Frame(box)
        top.pack(anchor="w")
        ttk.Label(top, text=tr("between.preview_title"), foreground=look.LABEL).grid(row=0, column=0, sticky="w")
        pv = self.pv = tk.Canvas(top, width=self.w, height=self.ph, bg=look.CHART_BG, highlightthickness=1,
                                 highlightbackground=look.CHART_BORDER)
        pv.grid(row=1, column=0, sticky="nw", pady=(2, 0))
        self.push_on, self.pad_drag = 0, None  # (which anchor the pad pushes: its number from the first shape)
        self.pad_was = None  # (the window's undo place before a pad press: a double-click's first click undone)
        marks = [sh for _, sh in between.anchors(app.shapes, gid)]
        self.push_on = next((j for j, sh in enumerate(marks) if sh is opened_on), 0)
        self.build_pad(top)
        ttk.Label(top, text=tr("between.preview_hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=self.w).grid(row=2, column=0, sticky="nw", pady=(2, 6))
        pv.bind("<ButtonPress-1>", self.pv_press)
        pv.bind("<B1-Motion>", self.pv_motion)
        pv.bind("<ButtonRelease-1>", self.pv_release)
        pv.bind("<Motion>", self.pv_move)
        ttk.Label(box, text=tr("between.graph"), foreground=look.LABEL).pack(anchor="w")
        mid = ttk.Frame(box)
        mid.pack(anchor="w", pady=(2, 4))
        cv = self.canvas = tk.Canvas(mid, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                     highlightbackground=look.CHART_BORDER, cursor="crosshair")
        cv.pack(side="left", anchor="n")
        side = ttk.Frame(mid)
        side.pack(side="left", anchor="n", padx=(8, 0))
        row = ttk.Frame(side)
        row.pack(anchor="w")
        lb = ttk.Label(row, text=tr("between.steps"))
        lb.pack(side="left")
        self.steps_var = tk.StringVar()
        e = self.steps_box = ttk.Entry(row, textvariable=self.steps_var, width=6)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: (self.on_steps(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_steps())
        Scrub(app, [(e, self.steps_var, self.on_steps)], (1, 10, 1), 1, between.MOST_STEPS, label=lb)
        for w in (lb, e):
            Tooltip(w, tr("between.steps_tip"))
        self.info = ttk.Label(side, text="", foreground=look.HINT)
        self.info.pack(anchor="w", pady=(2, 0))
        self.rev_var = tk.BooleanVar()
        rev = ttk.Checkbutton(side, text=tr("between.reverse"), variable=self.rev_var,
                              command=lambda: self.change("rev", self.rev_var.get()))
        rev.pack(anchor="w", pady=(6, 0))
        Tooltip(rev, tr("between.reverse_tip"))
        self.smooth_var = tk.BooleanVar()
        sm = ttk.Checkbutton(side, text=tr("between.smooth"), variable=self.smooth_var,
                             command=lambda: self.change("smooth", self.smooth_var.get()))
        sm.pack(anchor="w", pady=(4, 0))
        Tooltip(sm, tr("between.smooth_tip"))
        self.col_var = tk.BooleanVar()
        col = ttk.Checkbutton(side, text=tr("between.colours"), variable=self.col_var, command=self.on_colours)
        col.pack(anchor="w", pady=(4, 0))
        Tooltip(col, tr("between.colours_tip"))
        row = ttk.Frame(side)
        row.pack(anchor="w", padx=(int(20 * s), 0))
        self.turns_label = ttk.Label(row, text=tr("between.turns"))
        self.turns_label.pack(side="left")
        self.turns_var = tk.StringVar()
        e = self.turns_box = ttk.Entry(row, textvariable=self.turns_var, width=4)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: (self.on_turns(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_turns())
        Scrub(app, [(e, self.turns_var, self.on_turns)], (1, 5, 1), 1, between.COLOURS_MOST, label=self.turns_label)
        for w in (self.turns_label, e):
            Tooltip(w, tr("between.turns_tip"))
        self.multi_note = ttk.Label(side, text=tr("between.needs_multi"), foreground=look.WARN, font=look.font(8),
                                    wraplength=self.ph, justify="left")
        row = ttk.Frame(box)
        row.pack(anchor="w")
        for key, pts in PRESETS:
            ttk.Button(row, text=tr(key), command=lambda pts=pts: self.change("graph", [list(p) for p in pts])
                       ).pack(side="left", padx=(0, 4))
        ttk.Label(box, text=tr("between.hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=self.w).pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="e", pady=(6, 0))
        ttk.Button(row, text=tr("between.ok"), command=self.ok).pack(side="left")
        ttk.Button(row, text=tr("between.cancel"), command=self.cancel).pack(side="left", padx=(4, 0))
        cv.bind("<ButtonPress-1>", self.press)
        cv.bind("<B1-Motion>", self.motion)
        cv.bind("<ButtonRelease-1>", self.release)
        cv.bind("<ButtonPress-3>", self.delete_at)
        cv.bind("<Motion>", self.on_hover)
        cv.bind("<Leave>", lambda e: self.on_hover(None))
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show()
        self.hist = LocalUndo(self, self.state, self.put_state)
        self.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 120}+{app.winfo_rooty() + 120}")
        remember_place(self, "add_between")
        self.grab_set()  # (the group stays as it is while it's open)
        self.focus_set()

    # ---- the group

    def ends(self):
        """The group's first and last shape."""
        got = [self.app.shapes[i] for i in between.ordered(self.app.shapes, self.gid)]
        return got[0], got[-1]

    def anchors(self):
        """The group's first shape, keys (by place) and last shape: the shapes that can push."""
        return [sh for _, sh in between.anchors(self.app.shapes, self.gid)]

    def state(self):
        a, b = self.ends()
        pushes = [sh["between"].get("push") for sh in self.anchors()]
        return json.dumps([self.set, a["pts"], b["pts"], pushes, self.push_on], sort_keys=True)

    def put_state(self, state):
        self.drag = self.pv_drag = self.pad_drag = None
        self.app.scrubbing = False
        self.set, pa, pb, pushes, self.push_on = json.loads(state)
        a, b = self.ends()
        a["pts"], b["pts"] = pa, pb
        for sh, p in zip(self.anchors(), pushes):
            self.put_push(sh, p)
        self.apply()

    @staticmethod
    def put_push(sh, p):
        if p and (p[0] or p[1]):
            sh["between"]["push"] = [float(p[0]), float(p[1])]
        else:
            sh["between"].pop("push", None)

    def change(self, key, value, mark=True):
        self.set[key] = value
        self.apply()
        if mark:
            self.hist.mark()

    def apply(self):
        """The window's settings on the group: its steps made again, the whole group selected."""
        app = self.app
        app.roll.cancel_draft()
        if between.first_of(app.shapes, self.gid) is None:  # (gone: nothing to do)
            return self.show()
        app.shapes[:], got = between.rebuild(app.shapes, self.gid, self.set)
        self.set = between.settings_of(app.shapes, self.gid)
        app.select_many(got, got[0])
        app.shapes_changed()
        self.show()

    def show(self):
        n = self.set["steps"]
        self.steps_var.set(str(n))
        self.steps_box.config(style="TEntry")
        self.info.config(text=tr("between.in_all", n=len(between.members(self.app.shapes, self.gid))))
        self.rev_var.set(self.set["rev"])
        on = self.set["colours"] > 0
        self.col_var.set(on)
        if on:
            self.turns_var.set(str(self.set["colours"]))
        self.turns_box.config(state="normal" if on else "disabled")
        multi = self.app.channel_mode.get() == "auto"
        shown = bool(self.multi_note.winfo_manager())
        want = self.colours_touched and on and not multi
        if want and not shown:
            self.multi_note.pack(anchor="w", after=self.turns_box.master, pady=(2, 0))
        elif shown and not want:
            self.multi_note.pack_forget()
        self.smooth_var.set(self.set.get("smooth", False))
        self.draw()
        self.draw_preview()
        self.draw_pad()

    def on_steps(self):
        if self.closed:
            return
        try:
            n = calc(self.steps_var.get())
            if not 1 <= n <= between.MOST_STEPS:
                raise ValueError
        except (ValueError, ZeroDivisionError):  # (not a number, or out of range: the box goes back, user)
            return self.steps_var.set(str(self.set["steps"]))
        n = math.floor(n + 0.5)
        if n != self.set["steps"]:
            self.set["steps"] = n
            self.app.busy(tr("between.making"))  # (hundreds of long curves take a moment)
            self.config(cursor="watch")
            try:
                self.apply()
            finally:
                self.config(cursor="")
                self.app.busy(None)
            self.hist.mark("steps")
            return True

    def on_colours(self):
        self.colours_touched = True
        if self.set["colours"]:
            self.kept_turns = self.set["colours"]
        self.change("colours", self.kept_turns if self.col_var.get() else 0)

    def on_turns(self):
        if self.closed or str(self.turns_box.cget("state")) == "disabled":
            return
        try:
            n = calc(self.turns_var.get())
            if not 1 <= n <= between.COLOURS_MOST:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            return self.turns_var.set(str(self.set["colours"]))
        n = math.floor(n + 0.5)
        if n != self.set["colours"]:
            self.colours_touched = True
            self.set["colours"] = self.kept_turns = n
            self.apply()
            self.hist.mark("turns")
            return True

    def ok(self):
        if self.closed:
            return
        self.on_steps()  # (a number typed without Enter counts too)
        self.on_turns()
        self.closed = True
        if json.dumps(self.app.shapes) != self.before:
            self.app.add_undo_step(self.before, self.name, sel=self.app.sel_state())
        self.close()

    def cancel(self):
        if self.closed:
            return
        self.closed = True
        app = self.app
        app.roll.cancel_draft()
        app.shapes[:] = json.loads(self.before)
        sels, sel = self.sel_before
        app.select_many({i for i in sels if i < len(app.shapes)}, sel if sel is not None and sel < len(app.shapes)
                        else None)
        app.shapes_changed()
        self.close()

    def close(self):
        if self.app.scrubbing:  # (closed while the pad / a point was held: its notes may be late)
            self.app.scrubbing = False
            self.app.catch_up_notes()
        self.update()  # (after a long wait Windows shows a "not responding" copy of the window: destroyed before
        self.grab_release()  # that's gone, Tk crashed)
        self.destroy()

    # ---- the preview: the whole group, the first and last shapes' points can be dragged (user)

    def fit_view(self):
        """(beat at the left, beats per px, top key, keys per px) showing every shape of the group."""
        paths = [cached_path(self.app.shapes[i]) for i in between.ordered(self.app.shapes, self.gid)]
        a = np.concatenate([np.asarray(p, float).reshape(-1, 2) for p in paths] +
                           [np.asarray(sh["pts"], float) for sh in self.ends()])
        lo, hi = a.min(axis=0), a.max(axis=0)
        pad = int(14 * self.s)
        bw = max(hi[0] - lo[0], 1e-6) / (self.w - 2 * pad)
        kh = max(hi[1] - lo[1], 1.0) / (self.ph - 2 * pad)
        mid = (lo[1] + hi[1]) / 2
        return lo[0] - pad * bw, bw, mid + (self.ph / 2) * kh, kh

    def to_px(self, b, p):
        b0, bw, top, kh = self.pv_view
        return (b - b0) / bw, (top - p) / kh

    def px_line(self, sh, most=None):
        """A shape's line as preview coordinates (most: at most this many of its points, spread along it)."""
        a = np.asarray(cached_path(sh), float).reshape(-1, 2)
        if most and len(a) > most:
            a = a[np.unique(np.linspace(0, len(a) - 1, most).round().astype(int))]
        b0, bw, top, kh = self.pv_view
        return np.column_stack([(a[:, 0] - b0) / bw, (top - a[:, 1]) / kh]).ravel().tolist()

    def draw_preview(self):
        cv, s = self.pv, self.s
        cv.delete("all")
        if between.first_of(self.app.shapes, self.gid) is None:
            return
        if self.pv_drag is None:  # (still while a point is dragged)
            self.pv_view = self.fit_view()
        order = between.ordered(self.app.shapes, self.gid)
        most = max(16, min(400, PREVIEW_POINTS // max(1, len(order))))  # (500 long curves were 9 M points)
        marks = self.anchors()
        self.push_on = min(self.push_on, len(marks) - 1)
        xy = self.px_line(marks[self.push_on])  # (the shape the pad pushes: a wide pale band under it)
        if len(xy) >= 4:
            cv.create_line(*xy, fill=look.PUSH_PICKED, width=max(3, round(9 * s)), capstyle="round")
        for i in order[1:-1]:
            sh = self.app.shapes[i]
            colour = look.CHART_LINE_FAINT if sh["between"]["role"] == "step" else look.CHART_GRID_STRONG
            xy = self.px_line(sh, most)
            if len(xy) >= 4:
                cv.create_line(*xy, fill=colour, width=max(1, round(s)))
        lw = max(1, round(2 * s))
        r = 4 * s
        for sh, colour, name in zip(self.ends(), (look.VALUE, look.CHART_LINE), ("first", "last")):
            xy = self.px_line(sh)
            if len(xy) >= 4:
                cv.create_line(*xy, fill=colour, width=lw)
            pts = sh["pts"]
            if sh["kind"] == "curve":  # (handles: thin lines to their anchors)
                for j, (b, p) in enumerate(pts):
                    if j % 3:
                        a = pts[j - 1] if j % 3 == 1 else pts[j + 1]
                        cv.create_line(*self.to_px(*a), *self.to_px(b, p), fill=colour)
            for j, (b, p) in enumerate(pts):
                x, y = self.to_px(b, p)
                hot = self.pv_hover == (name, j) or self.pv_drag and self.pv_drag[:2] == (name, j)
                draw = cv.create_oval if sh["kind"] == "curve" and j % 3 else cv.create_rectangle
                draw(x - r, y - r, x + r, y + r, fill=colour if hot else look.CHART_POINT, outline=colour, width=lw)
            x, y = self.to_px(*pts[0])
            cv.create_text(x, y - 8 * s, text=tr("between." + name), anchor="s", fill=colour, font=look.font(7))

    def pv_point(self, x, y):
        """(first / last, point number) of the point under the mouse, or None."""
        if self.pv_view is None:
            return None
        best = None
        for sh, name in zip(self.ends(), ("first", "last")):
            for j, (b, p) in enumerate(sh["pts"]):
                px, py = self.to_px(b, p)
                d = max(abs(px - x), abs(py - y))
                if d <= 7 * self.s and (best is None or d < best[0]):
                    best = (d, name, j)
        return None if best is None else best[1:]

    def pv_move(self, e):
        hot = self.pv_point(e.x, e.y)
        self.pv.config(cursor="fleur" if hot else "")
        if hot != self.pv_hover:
            self.pv_hover = hot
            self.draw_preview()

    def pv_press(self, e):
        hit = self.pv_point(e.x, e.y)
        if hit:
            a, b = self.ends()
            sh = a if hit[0] == "first" else b
            self.pv_drag = (hit[0], hit[1], json.loads(json.dumps(sh["pts"])), e.x, e.y)
            self.app.scrubbing = True  # (slow notes wait for the mouse to rest / let go: App.shapes_changed)
            self.pick_push(0 if hit[0] == "first" else len(self.anchors()) - 1)
            return
        near = self.pv_line(e.x, e.y)
        if near is not None:
            self.pick_push(near)

    def pv_line(self, x, y):
        """The number of the first shape / key / last shape whose line is under the mouse (the nearest), or None."""
        best = None
        for j, sh in enumerate(self.anchors()):
            a = np.asarray(self.px_line(sh), float).reshape(-1, 2)
            if len(a) < 2:
                continue
            p, q = a[:-1], a[1:]
            d = q - p
            t = np.clip(((x - p[:, 0]) * d[:, 0] + (y - p[:, 1]) * d[:, 1]) / np.maximum((d * d).sum(1), 1e-12), 0, 1)
            dist = float(np.hypot(p[:, 0] + t * d[:, 0] - x, p[:, 1] + t * d[:, 1] - y).min())
            if dist <= 6 * self.s and (best is None or dist < best[0]):
                best = (dist, j)
        return None if best is None else best[1]

    def pick_push(self, j):
        if j != self.push_on:
            self.push_on = j
            self.draw_preview()
            self.draw_pad()

    def pv_motion(self, e):
        if not self.pv_drag:
            return
        name, j, was, x0, y0 = self.pv_drag
        _, bw, _, kh = self.pv_view
        db, dp = (e.x - x0) * bw, (y0 - e.y) * kh
        b, p = was[j][0] + db, was[j][1] + dp
        if not e.state & 0x1:  # (snapped like on the piano roll: the grid, whole keys; Shift = free)
            sb = self.app.snap_beats()
            b = round(b / sb) * sb if sb else b
            p = round(p)
        b = max(0.0, b)  # (beat 0 and the lowest / highest key stop it, like the piano roll: unless it's past already)
        p = min(max(p, min(0.0, was[j][1])), max(self.app.keys - 1.0, was[j][1]))
        db, dp = b - was[j][0], p - was[j][1]
        pts = json.loads(json.dumps(was))
        sh = self.ends()[0 if name == "first" else 1]
        moved = [j]
        if sh["kind"] == "curve" and j % 3 == 0:  # (an anchor takes its handles along, like the pen tool)
            moved += [k for k in (j - 1, j + 1) if 0 <= k < len(pts)]
        for k in moved:
            pts[k] = [pts[k][0] + db, pts[k][1] + dp]
        if pts != sh["pts"]:
            sh["pts"] = pts
            self.app.shapes_changed()
            self.info.config(text=tr("between.in_all", n=len(between.members(self.app.shapes, self.gid))))
            self.draw_preview()

    def pv_release(self, e):
        if self.pv_drag:
            self.pv_drag = None
            self.app.scrubbing = False
            self.app.catch_up_notes()
            self.draw_preview()
            self.hist.mark()

    # ---- the push pad (user): like a game controller's stick. Drag from the middle: the way = which way the steps
    # leave the picked shape, how far = how hard. The middle is sticky (Shift = not), Ctrl = 8 ways, double-click =
    # no push.

    def build_pad(self, parent):
        """The pad beside the preview (parent's grid column 1), as tall as it, its title on the preview's title row."""
        self.pad_title = ttk.Label(parent, text="", foreground=look.LABEL)
        self.pad_title.grid(row=0, column=1, sticky="w", padx=(8, 0))
        n = self.pad_size = self.ph
        pad = self.pad = tk.Canvas(parent, width=n, height=n, bg=look.CHART_BG, highlightthickness=1,
                                   highlightbackground=look.CHART_BORDER, cursor="crosshair")
        pad.grid(row=1, column=1, sticky="nw", padx=(8, 0), pady=(2, 0))
        pad.bind("<ButtonPress-1>", self.pad_press)
        pad.bind("<B1-Motion>", self.pad_motion)
        pad.bind("<ButtonRelease-1>", self.pad_release)
        pad.bind("<Double-Button-1>", self.pad_reset)
        self.pad_info = ttk.Label(parent, text="", foreground=look.HINT, font=look.font(8))
        self.pad_info.grid(row=2, column=1, sticky="nw", padx=(8, 0), pady=(2, 0))
        for w in (self.pad_title, self.pad_info):  # (not on the pad itself: it covered the stick, user)
            Tooltip(w, tr("between.push_tip"))

    def pad_radius(self):
        return self.pad_size / 2 - 10 * self.s

    def picked(self):
        marks = self.anchors()
        return marks[min(self.push_on, len(marks) - 1)]

    def draw_pad(self):
        cv, s = self.pad, self.s
        cv.delete("all")
        if between.first_of(self.app.shapes, self.gid) is None:
            return
        marks = self.anchors()
        j = min(self.push_on, len(marks) - 1)
        name = (tr("between.first") if j == 0 else tr("between.last") if j == len(marks) - 1
                else tr("between.key_n", n=j))
        self.pad_title.config(text=tr("between.push_on", name=name))
        c, r = self.pad_size / 2, self.pad_radius()
        cv.create_oval(c - r, c - r, c + r, c + r, outline=look.CHART_FRAME)
        cv.create_line(c - r, c, c + r, c, fill=look.CHART_GRID)
        cv.create_line(c, c - r, c, c + r, fill=look.CHART_GRID)
        st = PAD_STICK * s
        cv.create_oval(c - st, c - st, c + st, c + st, outline=look.CHART_GRID_STRONG, dash=(2, 2))
        x, y = between.push_of(marks[j])
        px, py = c + x * r, c - y * r
        colour = look.VALUE if j == 0 else look.CHART_LINE if j == len(marks) - 1 else look.CHART_GRID_STRONG
        lw = max(1, round(2 * s))
        if x or y:
            cv.create_line(c, c, px, py, fill=colour, width=lw, arrow="last", arrowshape=(8 * s, 10 * s, 3 * s))
        k = 6 * s
        cv.create_oval(px - k, py - k, px + k, py + k, fill=colour if self.pad_drag else look.CHART_POINT,
                       outline=colour, width=lw)
        strength = math.hypot(x, y)
        self.pad_info.config(text=tr("between.push_none") if not strength else
                             tr("between.push_amount", n=round(strength * 100),
                                a=round(math.degrees(math.atan2(y, x))) % 360))

    def pad_at(self, e):
        """The push for the mouse at e: Ctrl = 8 ways, the middle sticky unless Shift (user)."""
        c, r = self.pad_size / 2, self.pad_radius()
        x, y = (e.x - c) / r, (c - e.y) / r
        n = math.hypot(x, y)
        if n > 1:
            x, y, n = x / n, y / n, 1.0
        if not e.state & 0x1 and n * r <= PAD_STICK * self.s:
            return [0.0, 0.0]
        if e.state & 0x4 and n:
            a = round(math.atan2(y, x) / (math.pi / 4)) * (math.pi / 4)
            x, y = n * math.cos(a), n * math.sin(a)
        return [round(x, 4), round(y, 4)]

    def pad_press(self, e):
        if between.first_of(self.app.shapes, self.gid) is None:
            return
        self.pad_was = (self.hist.at, len(self.hist.states))
        self.pad_drag = True
        self.app.scrubbing = True  # (slow notes wait for the mouse to rest / let go: App.shapes_changed)
        self.pad_motion(e)

    def pad_motion(self, e):
        if not self.pad_drag:
            return
        self.set_push(self.pad_at(e))

    def set_push(self, p):
        sh = self.picked()
        if between.push_of(sh) == ([0.0, 0.0] if not (p[0] or p[1]) else p):
            return self.draw_pad()
        self.app.roll.cancel_draft()
        self.put_push(sh, p)
        self.app.shapes_changed()
        self.draw_preview()
        self.draw_pad()

    def pad_release(self, e):
        if self.pad_drag:
            self.pad_drag = None
            self.app.scrubbing = False
            self.app.catch_up_notes()
            self.draw_pad()
            self.hist.mark()

    def pad_reset(self, e):
        """Double-click: no push. Its first click already pushed and made an undo step: that step is dropped, so
        Ctrl+Z gives back the push from before the double-click (hunt 2026-10-08)."""
        self.pad_drag = None
        self.app.scrubbing = False
        h, was = self.hist, self.pad_was
        if was and h.at == was[0] + 1 and len(h.states) == was[1] + 1:
            del h.states[h.at:]
            h.at -= 1
        self.set_push([0.0, 0.0])
        self.app.catch_up_notes()
        h.mark()

    # ---- the graph (left = the first shape, right = the last; up = changed more towards the last)

    @property
    def pts(self):
        return self.set["graph"]

    def u2x(self, u):
        return self.ml + u * (self.w - self.ml - self.mr)

    def y2c(self, y):
        return self.mt + (1 - y) * (self.h - self.mt - self.mb)

    def x2u(self, x):
        return min(1.0, max(0.0, (x - self.ml) / (self.w - self.ml - self.mr)))

    def c2y(self, c):
        return min(1.0, max(0.0, 1 - (c - self.mt) / (self.h - self.mt - self.mb)))

    def draw(self):
        cv, s = self.canvas, self.s
        cv.delete("all")
        x0, x1, top, bot = self.u2x(0), self.u2x(1), self.y2c(1), self.y2c(0)
        for j in range(1, 4):
            cv.create_line(x0, self.y2c(j / 4), x1, self.y2c(j / 4), fill=look.CHART_GRID)
        cv.create_rectangle(x0, top, x1, bot, outline=look.CHART_FRAME)
        for k, text in ((0, tr("between.first")), (1, tr("between.last"))):
            cv.create_text(x0 - 4 * s, self.y2c(k), text=text, anchor="e", fill=look.LABEL, font=look.font(7))
        cv.create_text(x0, bot + 4 * s, text=tr("between.first"), anchor="nw", fill=look.LABEL, font=look.font(7))
        cv.create_text(x1, bot + 4 * s, text=tr("between.last"), anchor="ne", fill=look.LABEL, font=look.font(7))
        n = self.set["steps"]
        shapes = self.app.shapes  # (where each step / key is: a tick on the line)
        for u in [shapes[i]["between"]["at"] for i in between.ordered(shapes, self.gid)[1:-1]]:
            x, y = self.u2x(u), self.y2c(between.change_at(self.pts, u))
            cv.create_line(x, bot, x, y, fill=look.CHART_LINE_FAINT)
        lw = max(1, round(1.5 * s))
        cv.create_line(*[c for u, y in self.pts for c in (self.u2x(u), self.y2c(y))], fill=look.CHART_LINE, width=lw)
        r = 4 * s
        for i, (u, y) in enumerate(self.pts):
            x, yy = self.u2x(u), self.y2c(y)
            shape = cv.create_rectangle if i in (0, len(self.pts) - 1) else cv.create_oval
            shape(x - r, yy - r, x + r, yy + r, fill=look.CHART_POINT, outline=look.CHART_LINE, width=lw)
        at = self.pts[self.drag] if self.drag is not None else self.hover
        if at is not None:
            cv.create_text(x1 - 4 * s, top + 4 * s, anchor="ne", fill=look.VALUE, font=look.font(8),
                           text=tr("between.at", n=round(at[0] * (n + 1), 1), y=round(at[1] * 100)))

    def point_at(self, x, y):
        r = 8 * self.s
        best = None
        for i, (u, v) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.y2c(v) - y))
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def snapped(self, e):
        u, y = self.x2u(e.x), self.c2y(e.y)
        if not e.state & 0x1:
            u, y = round(u / U_SNAP) * U_SNAP, round(y / Y_SNAP) * Y_SNAP
        return u, y

    def press(self, e):
        i = self.point_at(e.x, e.y)
        if i in (0, len(self.pts) - 1):  # (the ends are the first and last shapes: they stay)
            return
        if i is None:
            u, y = self.snapped(e)
            if u <= 1e-9 or u >= 1 - 1e-9:
                return
            pts = [list(p) for p in self.pts]
            i = next(j for j, p in enumerate(pts) if p[0] > u)
            pts.insert(i, [u, y])
            self.change("graph", pts, mark=False)
        self.drag = i
        self.draw()

    def motion(self, e):
        i = self.drag
        if i is None:
            return
        u, y = self.snapped(e)
        pts = [list(p) for p in self.pts]
        u = min(max(u, pts[i - 1][0]), pts[i + 1][0])
        if [u, y] != pts[i]:
            pts[i] = [u, y]
            self.change("graph", pts, mark=False)

    def release(self, e):
        if self.drag is None:
            return
        self.drag = None
        self.draw()
        self.hist.mark()

    def delete_at(self, e):
        i = self.point_at(e.x, e.y)
        if i is None or i in (0, len(self.pts) - 1) or self.drag is not None:
            return
        pts = [list(p) for p in self.pts]
        del pts[i]
        self.change("graph", pts)

    def on_hover(self, e):
        self.hover = None
        if e is not None and self.drag is None and self.ml <= e.x <= self.w - self.mr:
            u = self.x2u(e.x)
            self.hover = [u, between.change_at(self.pts, u)]
        self.draw()


class BetweenGroups:
    """Mixed into App: Add between groups in the main window."""

    def between_pair(self):
        """The two selected shapes Add between can start from, or None."""
        sels = sorted(i for i in self.sels if i < len(self.shapes))
        if len(sels) == 2 and all(between.usable(self.shapes[i]) for i in sels):
            return sels
        return None

    def add_between(self):
        """Right-click → Add between… on two open lines: the group made, the window opened."""
        pair = self.between_pair()
        if not pair:
            return
        self.roll.cancel_draft()
        before = json.dumps(self.shapes)
        self.shapes[:], gid = between.start_group(self.shapes, *pair)
        got = between.ordered(self.shapes, gid)
        self.select_many(got, got[0])
        self.shapes_changed()
        BetweenWindow(self, gid, before, tr("between.step_add"))
        self.tips.show("between", wait=True)

    def edit_between(self, i):
        """The window for the group shape i is in."""
        gid = between.group_of(self.shapes[i])
        if not gid:
            return
        self.roll.cancel_draft()
        got = between.ordered(self.shapes, gid)
        self.select_many(got, got[0])
        BetweenWindow(self, gid, json.dumps(self.shapes), tr("between.step_edit"), opened_on=self.shapes[i])

    def select_group(self, i):
        got = between.ordered(self.shapes, between.group_of(self.shapes[i]))
        self.select_many(got, got[0])

    def unkey(self, i):
        """A key shape back to an ordinary step (made again from the shapes around it)."""
        self.push_undo(name=tr("between.unkey"))
        b = self.shapes[i]["between"]
        b["role"] = "step"
        b.pop("sig", None)
        b.pop("push", None)
        self.shapes_changed()
        self.sync_panel()

    def group_velocity(self, i):
        """A step with a velocity of its own (changed by hand) takes the group's velocity again."""
        self.push_undo(name=tr("between.group_vel"))
        b = self.shapes[i]["between"]
        b.pop("vel", None)
        b.pop("sig", None)
        self.shapes_changed()
        self.sync_panel()

    def unlink_between(self, i):
        """The group shape i is in becomes plain shapes."""
        self.push_undo(name=tr("between.unlink"))
        between.unlink(self.shapes, between.group_of(self.shapes[i]))
        self.shapes_changed()
        self.sync_panel()

    def unlink_groups(self, indices):
        """Every group with one of these shapes in it becomes plain shapes (before Join / Turn into live shape /
        Split / Slice change them: user)."""
        for gid in {between.group_of(self.shapes[i]) for i in indices if i < len(self.shapes)} - {None}:
            between.unlink(self.shapes, gid)

    def between_part(self, sh):
        """" — first shape of an Add between" for the panel's title (None when it's not in a group)."""
        b = sh.get("between")
        if not b:
            return None
        if b["role"] in ("first", "last", "key"):
            return tr("between.part_" + b["role"])
        got = [self.shapes[i] for i in between.ordered(self.shapes, b["id"])]
        steps = [s for s in got if s["between"]["role"] in ("step", "key")]
        k = next((n for n, s in enumerate(steps, 1) if s is sh), 0)
        return tr("between.part_step", k=k, n=len(steps))

    # ---- the Shapes list: a group is one row (user)

    def shape_rows(self):
        """The Shapes list's rows: each a list of shape numbers (a group's: first, steps, last)."""
        rows, seen = [], set()
        for i, sh in enumerate(self.shapes):
            gid = between.group_of(sh)
            if gid is None:
                rows.append([i])
            elif gid not in seen:
                seen.add(gid)
                rows.append(between.ordered(self.shapes, gid))
        return rows

    def row_of(self, i):
        return next((r for r, row in enumerate(self.shape_rows()) if i in row), None)

    def fill_shape_list(self, counts, chans, many):
        """The Shapes list from scratch (counts / chans: per shape; many: the channel count shown orange)."""
        self.listbox.delete(0, "end")
        rendered = getattr(self, "rendered", None)
        for r, row in enumerate(self.shape_rows()):
            i = row[0]
            sh = self.shapes[i]
            if len(row) > 1 or between.group_of(sh):
                used = chans[i]
                if rendered is not None and len(rendered) and self.channel_mode.get() == "auto":
                    used = len(np.unique(rendered[np.isin(rendered[:, 5], row), 4]))
                uses = tr("app.channels_2", chans=used) if used > 1 else ""
                n = sum(1 for j in row if self.shapes[j]["between"]["role"] in ("step", "key"))
                text = tr("between.row", i=i + 1, n=n, counts=sum(counts[j] for j in row), uses=uses)
            else:
                used = chans[i]
                uses = tr("app.channels_2", chans=used) if used > 1 else ""
                text = tr("app.notes", i=i + 1, shape_label=self.shape_label(sh), counts=counts[i], uses=uses)
            self.listbox.insert("end", text)
            if used > many:  # (past this the note colours and channel numbers repeat)
                self.listbox.itemconfig(r, foreground=look.WARN, selectforeground=look.GAP_PICKED)
