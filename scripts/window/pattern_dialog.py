"""The Custom… window for patterns along curves (pattern.py): pick a preset or a saved pattern, change its numbers
(each other name in the formula gets a number box), or type a formula; the preview shows ONE loop with anchors and
handles, and dragging them changes the loop by hand (every loop along the curve follows). The user's saved patterns
are in spiderweb/patterns.json. Changes show on the piano roll straight away; Apply keeps them as one undo step,
Cancel puts back how it was."""

import copy
import json
import math
import os
import tkinter as tk
from tkinter import ttk, messagebox

from files.lang import tr
from files.mathexpr import calc, fmt, formula
from files.project import HERE
from files.safefile import write_text
from notes.bezier import (add_anchor, can_delete, delete_point, drag_point, fit, handle_lines, nearest,
                          pen_handles, sample)
from notes.pattern import (LOOPS_DEFAULT, PATTERN_PRESETS, clean_loop, formula_loop, loop_length, loop_points,
                           new_pattern)
from roll.roll_shared import ALT
from window.widgets import Scrub, Tooltip

PATTERNS_FILE = os.path.join(HERE, "patterns.json")
LOOP_TOLERANCE = 0.02  # how closely the anchors + handles follow the formula when the loop is first edited, in keys
GRAB = 7  # how near (pixels) a point has to be to be dragged


def load_patterns():
    """The user's saved patterns: [{"name", "formula", "vars", maybe "loop"}]."""
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for p in data.get("patterns", []):
            item = {"name": str(p["name"]), "formula": str(p.get("formula", "")),
                    "vars": {str(a): float(b) for a, b in dict(p.get("vars", {})).items()}}
            loop = clean_loop(p["loop"]) if isinstance(p.get("loop"), dict) else None
            if loop:
                item["loop"] = loop
            out.append(item)
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def save_patterns(items):
    write_text(PATTERNS_FILE, json.dumps({"patterns": items}, indent=1))


def saved_pattern(item, k, old=None):
    """A saved pattern (load_patterns) as a curve's pattern. k: beats per key on screen; old: the pattern it
    replaces (its loops and how it runs over pieces stay)."""
    old = old or {}
    out = {"preset": "", "name": item["name"], "formula": item["formula"], "vars": dict(item["vars"]),
           "loops": old.get("loops", LOOPS_DEFAULT), "each": old.get("each", False), "k": k, "mirror": False,
           "scale": 1.0}
    if item.get("loop"):
        out["loop"] = copy.deepcopy(item["loop"])
    return out


class PatternDialog(tk.Toplevel):
    """Custom… for the selected curves' pattern along them."""

    def __init__(self, app):
        super().__init__(app)
        self.title(tr("pattern_dialog.title"))
        self.transient(app)
        self.resizable(False, False)
        self.app = app
        self.targets = app.pattern_targets()
        self.before_json = json.dumps(app.shapes)
        self.before = [copy.deepcopy(sh.get("pattern")) for sh in self.targets]
        self.k = app.roll.sy / app.roll.sx if app.roll.sx else 0.25
        first = self.targets[0].get("pattern")
        self.pat = copy.deepcopy(first) if first else new_pattern("wave", self.k)
        self.ok = True      # the formula works
        self.view = None    # (scale in pixels per key, left x, middle y, loop width in pixels) while dragging
        self.drag = None    # the loop point being dragged
        self._loading = False
        s = self.scale = app.scale
        self.w, self.h = int(460 * s), int(250 * s)

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        left = ttk.LabelFrame(body, text=tr("pattern_dialog.patterns"), padding=6)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, height=16, width=22, activestyle="none", exportselection=False,
                                  font=("Segoe UI", 9))
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)
        ttk.Button(left, text=tr("pattern_dialog.delete"), command=self.delete).pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        right.pack(side="left", fill="both")
        right.columnconfigure(1, weight=1)
        self.name = tk.StringVar(value=self.pat.get("name") or self.preset_name())
        self.text = tk.StringVar(value=self.pat["formula"])
        ttk.Label(right, text=tr("pattern_dialog.name")).grid(row=0, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.name, width=30).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=1)
        ttk.Label(right, text=tr("pattern_dialog.y")).grid(row=1, column=0, sticky="w")
        row = ttk.Frame(right)
        row.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=1)
        self.entry = ttk.Entry(row, textvariable=self.text, width=34, font=("Consolas", 10), state="readonly")
        self.entry.pack(side="left", fill="x", expand=True)
        self.editing = tk.BooleanVar(value=False)
        b = ttk.Checkbutton(row, text=tr("pattern_dialog.edit_formula"), variable=self.editing,
                            command=self.on_edit_mode)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("pattern_dialog.edit_formula_tip"))
        ttk.Label(right, text=tr("pattern_dialog.help"), foreground="#777", font=("Segoe UI", 8),
                  wraplength=int(440 * s), justify="left").grid(row=2, column=0, columnspan=2, sticky="w",
                                                                pady=(3, 4))
        self.numbers = ttk.Frame(right)  # Loops, then a box per name in the formula
        self.numbers.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.boxes = {}
        self._names = None
        self.canvas = tk.Canvas(right, width=self.w, height=self.h, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#c0c0c0")
        self.canvas.grid(row=4, column=0, columnspan=2)
        c = self.canvas
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", self.release)
        c.bind("<Button-3>", self.right_click)
        c.bind("<Button-2>", self.add_point)
        c.bind("<Double-Button-1>", self.add_point)
        info = ttk.Frame(right)
        info.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.info = ttk.Label(info, text="", font=("Segoe UI", 9), wraplength=int(340 * s), justify="left")
        self.info.pack(side="left")
        self.back = ttk.Button(info, text=tr("pattern_dialog.back_to_formula"), command=self.back_to_formula)
        Tooltip(self.back, tr("pattern_dialog.back_to_formula_tip"))
        btns = ttk.Frame(right)
        btns.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text=tr("pattern_dialog.save"), command=self.save).pack(side="left")
        ttk.Button(btns, text=tr("pattern_dialog.cancel"), command=self.cancel).pack(side="right")
        ttk.Button(btns, text=tr("pattern_dialog.apply"), command=self.apply).pack(side="right", padx=4)

        self.text.trace_add("write", lambda *_: self.on_formula())
        self.bind("<Return>", lambda e: self.apply())
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.fill_list()
        for i, (kind, what, name) in enumerate(self.items):  # the pattern it has now, picked in the list
            if not self.pat.get("loop") or self.pat.get("name"):
                if (kind == "preset" and not self.pat.get("name") and what == self.pat.get("preset")
                        or kind == "saved" and name == self.pat.get("name")):
                    self.listbox.selection_set(i)
                    self.listbox.see(i)
        self.refresh()
        self.update_idletasks()  # over the middle of the main window
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()  # the roll can't change under it while it previews
        self.focus_set()

    def preset_name(self):
        return next((name for pid, name, _, _ in PATTERN_PRESETS if pid == self.pat.get("preset")), "")

    # ------------------------------------------------------------ the list
    def fill_list(self):
        self.saved = load_patterns()
        self.items = [("preset", pid, name) for pid, name, _, _ in PATTERN_PRESETS]
        self.items += [("saved", item, item["name"]) for item in self.saved]
        self.listbox.delete(0, "end")
        for kind, _, name in self.items:
            self.listbox.insert("end", name)
            if kind == "preset":
                self.listbox.itemconfig("end", foreground="#555")

    def on_pick(self, _):
        sel = self.listbox.curselection()
        if not sel:
            return
        kind, what, name = self.items[sel[0]]
        keep = {key: self.pat[key] for key in ("loops", "each")}
        self.pat = new_pattern(what, self.k) if kind == "preset" else saved_pattern(what, self.k)
        self.pat.update(keep)
        self._loading = True
        self.name.set(name)
        self.text.set(self.pat["formula"])
        self._loading = False
        self.ok = True
        self.refresh()

    def save(self):
        name = self.name.get().strip()
        if not self.ok and not self.pat.get("loop"):
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.doesn_t_work_yet"),
                                        parent=self)
        if not name:
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.name_first"),
                                        parent=self)
        if any(p["name"] == name for p in self.saved) and not messagebox.askyesno(
                tr("pattern_dialog.spiderweb"), tr("pattern_dialog.replace", name=name), parent=self):
            return
        item = {"name": name, "formula": self.pat["formula"], "vars": dict(self.pat["vars"])}
        if self.pat.get("loop"):
            item["loop"] = copy.deepcopy(self.pat["loop"])
        try:
            save_patterns([p for p in self.saved if p["name"] != name] + [item])
        except OSError as e:
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.couldn_t_save", e=e),
                                        parent=self)
        self.pat.update(name=name, preset="")
        self.fill_list()
        i = next(i for i, (kind, _, n) in enumerate(self.items) if kind == "saved" and n == name)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.listbox.see(i)
        self.show_on_roll()

    def delete(self):
        sel = self.listbox.curselection()
        if not sel or self.items[sel[0]][0] != "saved":
            return messagebox.showinfo(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.only_saved"), parent=self)
        name = self.items[sel[0]][2]
        if not messagebox.askyesno(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.delete_saved", name=name),
                                   parent=self):
            return
        try:
            save_patterns([p for p in self.saved if p["name"] != name])
        except OSError as e:
            return messagebox.showerror(tr("pattern_dialog.spiderweb"), tr("pattern_dialog.couldn_t_save", e=e),
                                        parent=self)
        self.fill_list()

    # ------------------------------------------------------------ the formula and its numbers
    def on_edit_mode(self):
        self.entry.config(state="normal" if self.editing.get() else "readonly")
        if self.editing.get():
            self.entry.focus_set()
            self.entry.icursor("end")

    def on_formula(self):
        if self._loading:
            return
        text = self.text.get()
        try:
            names = formula(text, named=True).names
        except ValueError as e:
            self.ok = False
            self.set_info(tr("pattern_dialog.can_t_use_it", e=e) if text.strip() else
                          tr("pattern_dialog.type_a_formula"), "#c00000" if text.strip() else "#777")
            return
        old = self.pat["vars"]
        if self.name.get() in [n for _, _, n in self.items]:  # it's not that preset / saved pattern any more
            self._loading = True
            self.name.set("")
            self._loading = False
        self.pat.update(formula=text, vars={n: old.get(n, 1.0) for n in names}, preset="")
        self.pat.pop("name", None)
        self.pat.pop("loop", None)
        self.refresh()

    def refresh(self):
        """Number boxes, the preview and the piano roll after a change."""
        names = ["loops"] + ([] if self.pat.get("loop") else list(self.pat["vars"]))
        if names != self._names:
            self.build_boxes(names)
        self._loading = True
        for name, (var, e) in self.boxes.items():
            var.set(fmt(self.pat["loops"] if name == "loops" else self.pat["vars"][name]))
            e.config(style="TEntry")
        self._loading = False
        try:
            loop_points(dict(self.pat, mirror=False, scale=1.0))
            self.ok = True
        except ValueError as e:
            self.ok = False
            self.set_info(tr("pattern_dialog.can_t_use_it", e=e), "#c00000")
        if self.ok:
            if self.pat.get("loop"):
                self.set_info(tr("pattern_dialog.edited_by_hand"), "#1d6b1d", back=True)
            else:
                self.set_info(tr("pattern_dialog.one_loop"), "#555")
        self.view = None
        self.draw()
        self.show_on_roll()

    def build_boxes(self, names):
        for w in self.numbers.winfo_children():
            w.destroy()
        self.boxes = {}
        self._names = names
        for name in names:
            cell = ttk.Frame(self.numbers)
            cell.pack(side="left", padx=(0, 10))
            lb = ttk.Label(cell, text=tr("panel_pattern.loops") if name == "loops" else name)
            lb.pack(side="left")
            var = tk.StringVar()
            e = ttk.Entry(cell, textvariable=var, width=6)
            e.pack(side="left", padx=(4, 0))
            e.bind("<Return>", lambda ev, n=name: (self.on_number(n), "break")[1])
            e.bind("<FocusOut>", lambda ev, n=name: self.on_number(n))
            loops = name == "loops"
            Scrub(self.app, [(e, var, lambda n=name: self.on_number(n))], (1, 10, 0.1) if loops else (0.5, 5, 0.1),
                  0.01 if loops else None, None, label=lb)
            tip = tr("panel_pattern.loops_tip") if loops else tr("panel_pattern.number_tip", name=name)
            for w in (lb, e):
                Tooltip(w, tip)
            self.boxes[name] = (var, e)

    def on_number(self, name):
        if self._loading or name not in self.boxes:
            return
        var, e = self.boxes[name]
        try:
            value = float(calc(var.get()))
            if not math.isfinite(value) or (name == "loops" and value <= 0):
                raise ValueError
            if name != "loops":
                formula_loop(dict(self.pat, vars=dict(self.pat["vars"], **{name: value})))
        except (ValueError, ZeroDivisionError):
            e.config(style="Bad.TEntry")
            return
        if name == "loops":
            self.pat["loops"] = value
        else:
            self.pat["vars"][name] = value
        self.refresh()

    def set_info(self, text, color, back=False):
        self.info.config(text=text, foreground=color)
        if back:
            self.back.pack(side="right")
        else:
            self.back.pack_forget()

    def back_to_formula(self):
        self.pat.pop("loop", None)
        self.refresh()

    # ------------------------------------------------------------ the piano roll
    def show_on_roll(self):
        """The pattern on every selected curve (each keeps its own screen proportions / mirroring)."""
        if not self.ok and not self.pat.get("loop"):
            return
        for sh in self.targets:
            own = sh.get("pattern") or {"k": self.k, "mirror": False, "scale": 1.0}
            new = copy.deepcopy(self.pat)
            new.update(k=own["k"], mirror=own["mirror"], scale=own["scale"])
            sh["pattern"] = new
        self.app.shapes_changed()
        self.app.sync_pattern()

    def apply(self):
        if not self.ok and not self.pat.get("loop"):
            return
        self.show_on_roll()
        self.destroy()
        self.app.push_undo(self.before_json, tr("panel_pattern.pattern_step", name=self.app_name()))
        self.app.sync_panel()

    def app_name(self):
        from notes.pattern import pattern_name
        return pattern_name(self.pat)

    def cancel(self):
        for sh, old in zip(self.targets, self.before):
            if old is None:
                sh.pop("pattern", None)
            else:
                sh["pattern"] = old
        self.destroy()
        self.app.shapes_changed()
        self.app.sync_panel()

    # ------------------------------------------------------------ the preview: one loop
    def loop_len(self):
        """One loop's length in keys, on the first selected curve (so the preview looks like the piano roll)."""
        sh = self.targets[0]
        own = sh.get("pattern") or {}
        try:
            got = loop_length(dict(sh, pattern=dict(self.pat, k=own.get("k", self.k))))
        except (ValueError, ZeroDivisionError, KeyError):
            got = 0.0
        return got if got > 1e-6 else 8.0

    def shown_loop(self):
        """The loop as a curve (anchors + handles in along 0 -> 1, keys): the one edited by hand, else fitted to the
        formula (not kept until something is dragged)."""
        if self.pat.get("loop"):
            return self.pat["loop"]
        u, v = formula_loop(self.pat)
        length = self.loop_len()
        corners = []
        pts = fit([(a * length, b) for a, b in zip(u.tolist(), v.tolist())], LOOP_TOLERANCE, corners)
        out = {"pts": [[x / length, y] for x, y in pts]}
        if corners:
            out["sharp"] = corners
        return out

    def fit_view(self, loop):
        """Pixels per key, the loop's left edge and the middle line: the loop as long as it is on the curve, as
        big as fits."""
        length = self.loop_len()
        pad = 24 * self.scale
        vs = [abs(b) for _, b in loop["pts"]] + [abs(b) for _, b in sample([tuple(p) for p in loop["pts"]], 32)]
        tall = max(vs + [0.5])
        s = min((self.w - 2 * pad) / length, (self.h / 2 - pad) / tall)
        width = s * length
        return s, (self.w - width) / 2, self.h / 2, width

    def to_xy(self, p):
        s, x0, ym, width = self.view
        return x0 + p[0] * width, ym - p[1] * s

    def from_xy(self, x, y):
        s, x0, ym, width = self.view
        return [(x - x0) / width, (ym - y) / s]

    def draw(self):
        c = self.canvas
        c.delete("all")
        if not self.ok and not self.pat.get("loop"):
            return
        loop = self.shown_loop()
        if self.view is None:
            self.view = self.fit_view(loop)
        s, x0, ym, width = self.view
        c.create_line(0, ym, self.w, ym, fill="#e0a0a0", dash=(6, 4))  # the curve itself (the origin path)
        pts = loop["pts"]
        line = [self.to_xy(p) for p in sample([tuple(p) for p in pts], 32)]
        for shift in (-1, 1):  # the loops before and after it, faint
            c.create_line(*[v for x, y in line for v in (x + shift * width, y)], fill="#d8d8e8", width=2)
        if self.pat.get("loop") and self.pat["formula"]:  # the formula it came from, faint under it
            try:
                u, v = formula_loop(self.pat)
                c.create_line(*[q for a, b in zip(u.tolist(), v.tolist()) for q in self.to_xy((a, b))],
                              fill="#d0d0d0", width=4)
            except ValueError:
                pass
        c.create_line(*[v for p in line for v in p], fill="#d01010", width=2)
        r = 3.5 * self.scale
        for a, h in handle_lines(pts):
            c.create_line(*self.to_xy(a), *self.to_xy(h), fill="#6080c0")
        for i, kind in pen_handles(pts):
            x, y = self.to_xy(pts[i])
            if kind == "ctrl":
                c.create_oval(x - r + 1, y - r + 1, x + r - 1, y + r - 1, fill="#0050d0", outline="")
            elif kind == "anchor":
                c.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#0050d0")
            else:
                c.create_rectangle(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#c00000")

    def point_at(self, x, y):
        if self.view is None or (not self.ok and not self.pat.get("loop")):
            return None
        pts = self.shown_loop()["pts"]
        best = None
        for i, _ in reversed(pen_handles(pts)):  # (anchors are drawn on top: they win)
            px, py = self.to_xy(pts[i])
            d = math.hypot(px - x, py - y)
            if d <= GRAB * self.scale and (best is None or d < best[0] - 0.5):
                best = (d, i)
        return best[1] if best else None

    def by_hand(self):
        """The loop from now on edited by hand (the formula's fitted curve to start with). It's no longer the saved
        pattern it came from (the preset it came from still names it)."""
        if not self.pat.get("loop"):
            self.pat["loop"] = copy.deepcopy(self.shown_loop())
        self.pat.pop("name", None)

    def press(self, e):
        self.drag = self.point_at(e.x, e.y)

    def motion(self, e):
        if self.drag is None:
            return
        self.by_hand()
        c = self.pat["loop"]
        last = len(c["pts"]) - 1
        drag_point(c, self.drag, self.from_xy(e.x, e.y), e.state & ALT, self.to_xy, self.from_xy)
        for i, handle, along in ((0, 1, 0.0), (last, last - 1, 1.0)):  # the ends stay at the loop's start and end
            d = along - c["pts"][i][0]  # (only up and down: an end dragged sideways comes back, its handle with it)
            if d:
                c["pts"][i][0] += d
                c["pts"][handle][0] += d
        self.set_info(tr("pattern_dialog.edited_by_hand"), "#1d6b1d", back=True)
        if self._names != ["loops"]:
            self.build_boxes(["loops"])
            self.boxes["loops"][0].set(fmt(self.pat["loops"]))
        self.draw()
        self.show_on_roll()

    def release(self, e):
        if self.drag is not None:
            self.drag = None
            self.view = None
            self.draw()

    def right_click(self, e):
        i = self.point_at(e.x, e.y)
        if i is None:
            return
        self.by_hand()
        if can_delete(self.pat["loop"], i) is None:
            return
        delete_point(self.pat["loop"], i, self.to_xy)
        self.refresh()

    def add_point(self, e):
        if self.view is None or (not self.ok and not self.pat.get("loop")):
            return
        loop = self.shown_loop()
        seg, t, d = nearest(loop["pts"], self.to_xy, e.x, e.y)
        if d > 3 * GRAB * self.scale:
            return
        self.by_hand()
        if add_anchor(self.pat["loop"], seg, t, self.from_xy(e.x, e.y), self.to_xy):
            self.refresh()
