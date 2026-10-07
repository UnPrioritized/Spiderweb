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
from window.widgets import LocalUndo, Scrub, Tooltip


def _power(k):
    return [[i / 8, (i / 8) ** k] for i in range(9)]


def _s_curve(k):
    return [[i / 8, (i / 8) ** k / ((i / 8) ** k + (1 - i / 8) ** k)] for i in range(9)]


PRESETS = [("between.even", between.STRAIGHT), ("between.near_first", _power(2)),
           ("between.near_last", _power(0.5)), ("between.near_ends", _s_curve(0.5)),
           ("between.near_middle", _s_curve(2))]
U_SNAP, Y_SNAP = 1 / 40, 1 / 20  # dragging moves points in these steps (Shift = free)


class BetweenWindow(tk.Toplevel):
    def __init__(self, app, gid, before, name):
        """gid: the group (made already); before: the shapes as JSON before the window (Cancel puts them back, OK
        makes one undo step from them, called name)."""
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
        pv = self.pv = tk.Canvas(box, width=self.w, height=self.ph, bg=look.CHART_BG, highlightthickness=1,
                                 highlightbackground=look.CHART_BORDER)
        pv.pack(anchor="w")
        ttk.Label(box, text=tr("between.preview_hint"), foreground=look.HINT, font=look.font(8), justify="left",
                  wraplength=self.w).pack(anchor="w", pady=(2, 6))
        pv.bind("<ButtonPress-1>", self.pv_press)
        pv.bind("<B1-Motion>", self.pv_motion)
        pv.bind("<ButtonRelease-1>", self.pv_release)
        pv.bind("<Motion>", self.pv_move)
        row = ttk.Frame(box)
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
        self.info = ttk.Label(row, text="", foreground=look.HINT)
        self.info.pack(side="left", padx=(4, 0))
        self.rev_var = tk.BooleanVar()
        rev = ttk.Checkbutton(box, text=tr("between.reverse"), variable=self.rev_var,
                              command=lambda: self.change("rev", self.rev_var.get()))
        rev.pack(anchor="w", pady=(6, 0))
        Tooltip(rev, tr("between.reverse_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(4, 0))
        self.col_var = tk.BooleanVar()
        col = ttk.Checkbutton(row, text=tr("between.colours"), variable=self.col_var, command=self.on_colours)
        col.pack(side="left")
        Tooltip(col, tr("between.colours_tip"))
        self.turns_label = ttk.Label(row, text=tr("between.turns"))
        self.turns_label.pack(side="left", padx=(6, 0))
        self.turns_var = tk.StringVar()
        e = self.turns_box = ttk.Entry(row, textvariable=self.turns_var, width=4)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: (self.on_turns(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_turns())
        Scrub(app, [(e, self.turns_var, self.on_turns)], (1, 5, 1), 1, between.COLOURS_MOST, label=self.turns_label)
        for w in (self.turns_label, e):
            Tooltip(w, tr("between.turns_tip"))
        self.multi_note = ttk.Label(box, text=tr("between.needs_multi"), foreground=look.WARN, font=look.font(8),
                                    wraplength=self.w, justify="left")
        ttk.Label(box, text=tr("between.graph"), foreground=look.LABEL).pack(anchor="w", pady=(8, 0))
        cv = self.canvas = tk.Canvas(box, width=self.w, height=self.h, bg=look.CHART_BG, highlightthickness=1,
                                     highlightbackground=look.CHART_BORDER, cursor="crosshair")
        cv.pack(anchor="w", pady=4)
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
        self.grab_set()  # (the group stays as it is while it's open)
        self.focus_set()

    # ---- the group

    def ends(self):
        """The group's first and last shape."""
        got = [self.app.shapes[i] for i in between.ordered(self.app.shapes, self.gid)]
        return got[0], got[-1]

    def state(self):
        a, b = self.ends()
        return json.dumps([self.set, a["pts"], b["pts"]], sort_keys=True)

    def put_state(self, state):
        self.drag = self.pv_drag = None
        self.set, pa, pb = json.loads(state)
        a, b = self.ends()
        a["pts"], b["pts"] = pa, pb
        self.apply()

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
        self.draw()
        self.draw_preview()

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
            self.apply()
            self.hist.mark("steps")

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

    def ok(self):
        self.on_steps()  # (a number typed without Enter counts too)
        self.on_turns()
        self.closed = True
        if json.dumps(self.app.shapes) != self.before:
            self.app.add_undo_step(self.before, self.name, sel=self.app.sel_state())
        self.close()

    def cancel(self):
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
        self.grab_release()
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

    def draw_preview(self):
        cv, s = self.pv, self.s
        cv.delete("all")
        if between.first_of(self.app.shapes, self.gid) is None:
            return
        if self.pv_drag is None:  # (still while a point is dragged)
            self.pv_view = self.fit_view()
        order = between.ordered(self.app.shapes, self.gid)
        for i in order[1:-1]:
            sh = self.app.shapes[i]
            colour = look.CHART_LINE_FAINT if sh["between"]["role"] == "step" else look.CHART_GRID_STRONG
            xy = [c for b, p in cached_path(sh) for c in self.to_px(b, p)]
            if len(xy) >= 4:
                cv.create_line(*xy, fill=colour, width=max(1, round(s)))
        lw = max(1, round(2 * s))
        r = 4 * s
        for sh, colour, name in zip(self.ends(), (look.VALUE, look.CHART_LINE), ("first", "last")):
            xy = [c for b, p in cached_path(sh) for c in self.to_px(b, p)]
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
        b = max(0.0, b)
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
            self.app.catch_up_notes()
            self.draw_preview()
            self.hist.mark()

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
        for u in between.slots(n):  # (where each step is: a tick on the line)
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
        BetweenWindow(self, gid, json.dumps(self.shapes), tr("between.step_edit"))

    def select_group(self, i):
        got = between.ordered(self.shapes, between.group_of(self.shapes[i]))
        self.select_many(got, got[0])

    def unkey(self, i):
        """A key shape back to an ordinary step (made again from the shapes around it)."""
        self.push_undo(name=tr("between.unkey"))
        b = self.shapes[i]["between"]
        b["role"] = "step"
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
