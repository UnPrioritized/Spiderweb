"""The Colour effects window (Effects… on the side panel's Recolour row, panel_recolour.py; Order of colour settings,
PLAN.md stage E, user 2026-11-10: a visible list in its own small window). It lists the first selected shape's colour
effects in the order they run (recolour.order_of: Shuffle / Split, each Stir tab, Warp, Liquify, each Reverse track
order step), the top one first. Drag a line up / down = another order (recolour.step_moved: a Stir past another Stir
= the tabs swap places); click a name = open that effect's own window (that Stir tab); × = take it away
(recolour.step_removed); Add ▾ = a new one at the end. Each change is one undo step and goes to every selected shape
whose effects are the same kinds in the same order (the others are left alone and a line says so, user's pick).
Effects a later Split: Clumps hides are greyed with a tip (hunt 11-10 #5). Follows the selection like Stir…."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from notes.custom import ROUND, pack_notes, unpack_notes
from notes.fx import is_page, pages
from notes.recolour import KEEP, MODES, PAGE, clean_recolour, hidden_by_clumps, new_seed, order_of, shape_moved, \
    shape_order, shape_step_removed, tags_of, turned_map
from window import look
from window.edit_line import step_name
from window.tool_window import TOOL_WINDOWS, ToolWindow
from window.widgets import ShortLabel, Tooltip, remember_place

START_AMOUNT = {"shuffle": 100.0, "scatter": 50.0, "clumps": 20.0}  # (as the side panel's dropdown: panel_recolour)
DRAG_PX = 4  # (a press moving less than this is a click: opens the effect)


class ColourOrderWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.colour_order_window = self
        self.title(tr("order.title"))
        self.transient(app)
        self.resizable(False, False)
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ShortLabel(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w", fill="x")
        self.list = ttk.Frame(box)
        self.list.pack(anchor="w", fill="x", pady=(8, 0))
        self.rows = []  # [(row frame, grip, name label, ×)] in order
        self.shown = None  # (the list shown: (kinds, names, greyed) - redrawn only when it changes)
        self.drop_line = tk.Frame(self.list, height=2, background=look.HANDLE)
        self.press = None  # {"row": place pressed, "y": its y on screen, "moved": bool, "to": the place it'd go}
        self.empty = ttk.Label(self.list, text=tr("order.none"), foreground=look.HINT)
        row = ttk.Frame(box)
        row.pack(anchor="w", fill="x", pady=(8, 0))
        self.add = ttk.Menubutton(row, text=tr("order.add"))
        self.add.pack(side="left")
        Tooltip(self.add, tr("order.add_tip"))
        self.add_menu = tk.Menu(self.add, tearoff=False, postcommand=self.fill_add)
        self.add["menu"] = self.add_menu
        ttk.Button(row, text=tr("merge_window.close"), command=self.close).pack(side="right")
        self.hint = ttk.Label(box, text=tr("order.hint"), foreground=look.HINT, wraplength=int(260 * app.scale),
                              justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(10, 0))
        self.others = ttk.Label(box, text="", foreground=look.HINT, wraplength=int(260 * app.scale), justify="left")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.sync()
        remember_place(self, "colour_order")
        self.deiconify()

    # ---- what it shows

    def targets(self):
        return self.app.recolour_targets()

    def first(self):
        """(the shown shape, its clean Recolour or None) or (None, None)."""
        tgts = self.targets()
        return (tgts[0], clean_recolour(tgts[0].get("recolour"))) if tgts else (None, None)

    def matching(self):
        """The selected shapes a change goes to: those whose steps are the same kinds in the same order as the shown
        shape's (the others are left alone)."""
        sh, rc = self.first()
        if sh is None:
            return []
        kinds = shape_order(sh)
        return [t for t in self.targets() if shape_order(t) == kinds]

    def sync(self):
        if not self.winfo_exists():
            return
        app = self.app
        tgts = self.targets()
        sh, rc = self.first()
        self.what.config(text=tr("order.nothing") if sh is None else
                         tr("order.shape", name=app.shape_label(sh)) if len(tgts) == 1 else
                         tr("order.n_shapes", n=len(tgts), name=app.shape_label(sh)))
        o = shape_order(sh) if sh is not None else []
        tags = tags_of(o)
        grey = hidden_by_clumps(rc, o) if rc else set()
        shown = (tuple(tags), tuple(step_name(rc, k, n, sh) for k, n in tags), tuple(sorted(grey)))
        if shown != self.shown:
            self.shown = shown
            self.build(rc, tags, grey, sh)
        same = len(self.matching())
        left = len(tgts) - same
        text = "\n".join(x for x in (trn("order.others", same - 1, n=same - 1) if same > 1 and left else "",
                                      trn("order.left", left, n=left) if left else "") if x)
        if text != self.others.cget("text"):
            self.others.config(text=text)
            if text:
                self.others.pack(anchor="w", fill="x", pady=(6, 0), after=self.hint)
            else:
                self.others.pack_forget()
        self.add.state(["!disabled" if tgts else "disabled"])

    def build(self, rc, tags, grey, sh=None):
        for r in self.rows:
            r[0].destroy()
        self.rows = []
        self.empty.pack_forget()
        if not tags:
            if self.first()[0] is not None:
                self.empty.pack(anchor="w")
            return
        for p, (kind, n) in enumerate(tags):
            off = p in grey
            r = ttk.Frame(self.list)
            r.pack(anchor="w", fill="x", pady=1)
            grip = ttk.Label(r, text="≡", foreground=look.HINT, cursor="fleur", padding=(0, 0, 6, 0))
            grip.pack(side="left")
            Tooltip(grip, tr("order.drag_tip"))
            num = ttk.Label(r, text=f"{p + 1}.", foreground=look.HINT, width=3)
            num.pack(side="left")
            name = ttk.Label(r, text=step_name(rc, kind, n, sh), foreground=look.LABEL_OFF if off else "",
                             cursor="hand2" if kind != "turned" else "fleur")
            name.pack(side="left")
            tip = tr("order.clumps_hidden") if off else tr("order.reverse_tip") if kind == "turned" \
                else tr("order.page_tip") if kind == PAGE else tr("order.open_tip")
            Tooltip(name, tip)
            x = ttk.Label(r, text="×", foreground=look.CLOSE, cursor="hand2", padding=(10, 0, 2, 0))
            x.pack(side="right")
            x.bind("<Enter>", lambda e, x=x: x.config(foreground=look.CLOSE_HOT))
            x.bind("<Leave>", lambda e, x=x: x.config(foreground=look.CLOSE))
            x.bind("<ButtonRelease-1>", lambda e, p=p: self.on_remove(e, p))
            Tooltip(x, tr("order.remove_tip"))
            for w in (r, grip, num, name):
                w.bind("<ButtonPress-1>", lambda e, p=p: self.on_press(e, p))
                w.bind("<B1-Motion>", self.on_motion)
                w.bind("<ButtonRelease-1>", self.on_release)
            self.rows.append((r, grip, name, x))

    # ---- drag to reorder, click to open

    def on_press(self, e, p):
        self.press = {"row": p, "y": e.y_root, "moved": False, "to": p}

    def place_at(self, y_root):
        """Where a line let go at y_root (on screen) would go: before the row whose middle is below it."""
        for p, r in enumerate(self.rows):
            f = r[0]
            if y_root < f.winfo_rooty() + f.winfo_height() / 2:
                return p
        return len(self.rows)

    def on_motion(self, e):
        pr = self.press
        if pr is None:
            return
        if not pr["moved"] and abs(e.y_root - pr["y"]) < DRAG_PX:
            return
        pr["moved"] = True
        slot = self.place_at(e.y_root)  # (a gap: 0 = above the first row .. len = below the last)
        pr["to"] = slot - (slot > pr["row"])  # (the place the line takes once it's out of the list)
        if self.rows:
            ref = self.rows[min(slot, len(self.rows) - 1)][0]
            y = ref.winfo_y() - 1 if slot < len(self.rows) else ref.winfo_y() + ref.winfo_height()
            self.drop_line.place(x=0, y=y, relwidth=1.0)
            self.drop_line.lift()

    def on_release(self, e):
        pr, self.press = self.press, None
        self.drop_line.place_forget()
        if pr is None:
            return
        if pr["moved"]:
            if pr["to"] != pr["row"]:
                self.move(pr["row"], pr["to"])
        else:
            self.open_step(pr["row"])

    def open_step(self, p):
        """The effect at place p of the shown shape's order: its own window (that Stir tab)."""
        sh, rc = self.first()
        tags = tags_of(shape_order(sh)) if sh is not None else []
        if not 0 <= p < len(tags):
            return
        kind, n = tags[p]
        app = self.app
        if kind == PAGE:  # (that note tool page's own window, on its tab)
            open_page(app, sh, n)
        elif kind == "mode":
            app.open_shares()
        elif kind == "stir":
            app.open_stir()
            if app.stir_window:
                app.stir_window.show_tab(n)
        elif kind == "warp":
            app.open_warp()
        elif kind == "liquify":
            app.open_liquify()

    def move(self, a, b):
        """Step a of the shown order to place b, for every matching shape (one undo step)."""
        sh = self.first()[0]
        if sh is None:
            return
        kinds = shape_order(sh)
        self.app.change_recolour(lambda old, t: shape_moved(t, a, b) if shape_order(t) == kinds else old,
                                 tr("order.undo_move"))
        self.sync()

    def on_remove(self, e, p):
        if not (0 <= e.x < e.widget.winfo_width() and 0 <= e.y < e.widget.winfo_height()):
            return  # (let go off the ×, like a button)
        sh = self.first()[0]
        if sh is not None:
            self.app.colour_step_removed(p, shape_order(sh))
        self.sync()

    # ---- Add ▾

    def fill_add(self):
        m = self.add_menu
        m.delete(0, "end")
        app = self.app
        sh, rc = self.first()
        has = order_of(rc) if sh is not None else []
        for mode in MODES:  # (one Shuffle / Split a shape: greyed while it has one)
            m.add_command(label=tr("recolour." + mode), command=lambda mode=mode: self.add_mode(mode),
                          state="disabled" if "mode" in has else "normal")
        m.add_separator()
        m.add_command(label=tr("stir.button"), command=self.add_stir)
        m.add_command(label=tr("warp.button"), command=app.open_warp)
        m.add_command(label=tr("liquify.button"), command=app.open_liquify)
        m.add_command(label=tr("join_split.reverse_tracks"), command=app.reverse_tracks,
                      state="normal" if app.reverse_targets() else "disabled")

    def add_mode(self, mode):
        self.app.change_recolour(lambda old, t: old if old and old["mode"] in MODES else
                                 dict(old or {"seed": new_seed()}, mode=mode, amount=START_AMOUNT[mode]),
                                 tr("order.undo_add"))
        self.sync()

    def add_stir(self):
        """A new Stir tab at the end (the Stir window's "+": saved at the end once one of its settings changes)."""
        app = self.app
        app.open_stir()
        w = app.stir_window
        if w and not w.fresh and order_of(self.first()[1]).count("stir"):
            w.on_add()

    def close(self):
        self.app.colour_order_window = None
        self.destroy()
        self.app.roll.focus_set()


def remove_step(app, p, kinds):
    """App.colour_step_removed: step p taken away from every selected shape whose steps are kinds (one undo step).
    A Reverse step on notes whose stored colours it swapped: once no Recolour keeps that swap, the notes get their
    colours back (else the Reverse would stay, hidden in the notes)."""
    tgts = [t for t in app.recolour_targets() if shape_order(t) == kinds]
    if not tgts or not 0 <= p < len(kinds):
        return False
    app.commit_typing()
    if app.notes_late:
        app.shapes_changed(now=True)
    for attr in TOOL_WINDOWS:  # (a note tool window's try kept first: it follows the pages there are now)
        w = getattr(app, attr, None)
        if w:
            w.settle()
    app.push_undo(name=tr("order.undo_remove"))
    for t in tgts:
        old = clean_recolour(t.get("recolour"))
        new = shape_step_removed(t, p)
        if kinds[p] == PAGE:  # (a note tool page: taken off the shape, its place in the order too, stage F)
            n = kinds[:p].count(PAGE)
            fx = list(t.get("fx") or [])
            at = [k for k, st in enumerate(fx) if is_page(st)]
            if n < len(at):
                ToolWindow.put_fx(t, fx[:at[n]] + fx[at[n] + 1:])
        if old is None:
            continue
        tw = old.get("turned")
        if tw and "notes" in t and not (new or {}).get("turned"):
            rows = unpack_notes(t["notes"]).copy()
            low = rows[:, 4] % ROUND
            rows[:, 4] = rows[:, 4] - low + turned_map(tw, back=True)(low)
            t["notes"] = pack_notes(np.asarray(rows, np.int64))
        if new:
            t["recolour"] = new
        else:
            t.pop("recolour", None)
    app.colour_edit_set(None)
    app.shapes_changed()
    app.sync_recolour()
    app.schedule_autosave()
    return True


PAGE_WINDOWS = {"claw": ("window.claw_window", "open_claw", "claw_window"),
                "strum": ("window.strum_window", "open_strum", "strum_window"),
                "chop": ("window.chop_window", "open_chop", "chop_window"),
                "trim": ("window.trim_window", "open_trim", "trim_window"),
                "path": ("window.path_window", "open_path", "path_window")}


def open_page(app, sh, n):
    """Shape sh's n-th note tool page: its tool's window opened on that page's tab (stage F)."""
    import importlib
    fx = sh.get("fx") or []
    at = [k for k, st in enumerate(fx) if is_page(st)]
    if n >= len(at) or fx[at[n]]["tool"] not in PAGE_WINDOWS:
        return
    mod, fn, attr = PAGE_WINDOWS[fx[at[n]]["tool"]]
    getattr(importlib.import_module(mod), fn)(app)
    w = getattr(app, attr, None)
    if w and w.targets and w.same() and w.app.shapes[w.targets[0]] is sh:
        w.pick_page(at[n])


def open_colour_order(app):
    w = getattr(app, "colour_order_window", None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        ColourOrderWindow(app)
