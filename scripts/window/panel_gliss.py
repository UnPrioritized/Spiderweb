"""The side panel's Glissando settings (notes/gliss.py): which grids a run's step picks from, and what it picked."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.gliss import (DEFAULTS, GRID_KINDS, MOST_COPIES, MOST_LEN, UNITS, auto_name, fit_pts, grid_ticks, grids,
                         pick_key, pick_parts, picked, plan, read_list)
from roll.roll_shared import NOTE_NAMES
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, unchanged


class GlissPanel:
    """Mixed into App."""

    def _build_gliss(self):
        box = self.gliss_box = ttk.Frame(self.settings)
        grid = ttk.Frame(box)  # (label column + controls: Grids, My list, Step, Thickness, Note length)
        grid.pack(fill="x")

        def label(r, key):
            lb = ttk.Label(grid, text=tr("panel_gliss." + key))
            lb.grid(row=r, column=0, sticky="w", pady=(0 if r == 0 else 3, 0))
            Tooltip(lb, tr("panel_gliss." + key + "_tip"))
            return lb

        label(0, "grids")
        cb = self.gliss_grids = ttk.Combobox(grid, state="readonly", width=18,
                                              values=(tr("panel_gliss.halves"), tr("panel_gliss.my_list")))
        cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=(5, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_gliss_grids())
        Tooltip(cb, tr("panel_gliss.grids_tip"))
        self.gliss_var = tk.StringVar()
        e = self.gliss_list = ttk.Entry(grid, textvariable=self.gliss_var, width=24)
        e.grid(row=1, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(3, 0))
        e.bind("<Return>", lambda ev: self.on_gliss_list())
        leave_box(self, e, self.gliss_var, self.on_gliss_list)
        Tooltip(e, tr("panel_gliss.list_tip"))
        label(2, "step")
        cb = self.gliss_step = ttk.Combobox(grid, state="readonly", width=12)
        cb.grid(row=2, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(3, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_gliss_step())
        Tooltip(cb, tr("panel_gliss.step_tip"))
        self.gliss_nums = {}
        label(3, "copies")
        both = ttk.Frame(grid)  # (Copies  above [n]  below [n]: one dotted line above the run, one below)
        both.grid(row=3, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(3, 0))
        for r, key, steps in ((3, "above", (1, 5, 1)), (3, "below", (1, 5, 1)), (4, "len", (5, 25, 1))):
            if r == 3:
                lb = ttk.Label(both, text=tr("panel_gliss." + key))
                lb.pack(side="left", padx=(0 if key == "above" else 8, 4))
                Tooltip(lb, tr("panel_gliss.copies_tip"))
                e = ttk.Entry(both, width=4)
                e.pack(side="left")
            else:
                lb = label(r, key)
                e = ttk.Entry(grid, width=7)
                e.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=(3, 0))
            var = tk.StringVar()
            e.config(textvariable=var)
            e.bind("<Return>", lambda ev, k=key: self.on_gliss_number(k))
            leave_box(self, e, var, lambda left, k=key: self.on_gliss_number(k, left))
            Scrub(self, [(e, var, lambda k=key: self.on_gliss_number(k))], steps, label=lb)
            Tooltip(e, tr("panel_gliss." + ("copies" if r == 3 else key) + "_tip"))
            self.gliss_nums[key] = (e, var)
        cb = self.gliss_unit = ttk.Combobox(grid, state="readonly", width=10,
                                             values=[tr("panel_gliss.unit_" + u) for u in UNITS])
        cb.grid(row=4, column=2, sticky="w", padx=(4, 0), pady=(3, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_gliss_unit())
        Tooltip(cb, tr("panel_gliss.len_tip"))
        self.gliss_info = ttk.Label(box, text="", foreground=look.HINT)
        self.gliss_info.pack(fill="x", pady=(2, 0))

    def gliss_targets(self):
        """What the Glissando settings change: the selected runs, or (nothing selected, Glissando tool) the settings
        new runs start with."""
        runs = [self.shapes[i] for i in sorted(self.sels) if i < len(self.shapes) and self.shapes[i]["kind"] == "gliss"]
        if runs:
            return runs
        return [] if self.sels or self.tool.get() != "gliss" else [{"gliss": self.gliss_defaults}]

    def sync_gliss(self):
        tgts = self.gliss_targets()
        self._rows["gliss"] = bool(tgts)
        self.layout_rows()
        if not tgts:
            return
        g = tgts[0]["gliss"]
        self._loading = True
        self.gliss_grids.current(GRID_KINDS.index(g.get("grids", "halves")))
        if g.get("grids") == "list":
            self.gliss_list.grid()
        else:
            self.gliss_list.grid_remove()
        self.gliss_var.set(g.get("list", DEFAULTS["list"]))
        good(self.gliss_list)
        names = self.gliss_step_names(tgts[0])
        self.gliss_step.config(values=[tr("panel_gliss.auto", grid=names[0])] + names[1:])
        step = g.get("step", "auto")
        self.gliss_step.current(0 if step == "auto" else names.index(step, 1))
        for key, (e, var) in self.gliss_nums.items():
            var.set(fmt(g.get(key, DEFAULTS[key])))
            good(e)
        self.gliss_unit.current(UNITS.index(g.get("unit", "pct")))
        self._loading = False
        lines = [self.picks_text(g)]
        if "kind" in tgts[0]:  # a run: how many keys
            keys = plan(tgts[0], self.ppq)[3]
            lines.insert(0, tr("panel_gliss.info", keys=trn("n.keys", len(keys), n=len(keys))))
        self.gliss_info.config(text="\n".join(lines))

    def gliss_step_names(self, t):
        """[the grid Auto would pick, then every grid the Step dropdown offers] (the fixed step too when the grid list
        no longer has it)."""
        g = t["gliss"]
        names = grids(g, self.ppq)
        step = g.get("step", "auto")
        if step != "auto" and step not in names:
            names = sorted(names + [step], key=lambda n: -grid_ticks(n, self.ppq))
        auto = auto_name(t, self.ppq) if "kind" in t else tr("panel_gliss.from_line")
        return [auto] + names

    def on_gliss_step(self):
        if self._loading:
            return
        tgts = self.gliss_targets()
        if tgts:
            i = self.gliss_step.current()
            self.set_gliss(_key=("gliss_step", tuple(sorted(self.sels))),
                           step="auto" if i <= 0 else self.gliss_step_names(tgts[0])[i])
        self.roll.focus_set()

    def on_gliss_unit(self):
        """% of the step <-> ticks: the length is changed so the notes stay as long as they were (the first run's)."""
        if self._loading:
            return
        tgts = self.gliss_targets()
        unit = UNITS[self.gliss_unit.current()]
        if tgts and tgts[0]["gliss"].get("unit", "pct") != unit:
            g = tgts[0]["gliss"]
            step = plan(tgts[0], self.ppq)[1] if "kind" in tgts[0] else self.ppq / 4
            n = g.get("len", 100)
            n = n / 100 * step if unit == "ticks" else n / step * 100
            n = max(1, round(n)) if unit == "ticks" else round(n, 2)
            self.set_gliss(_key=("gliss_len", tuple(sorted(self.sels))), unit=unit, len=min(n, MOST_LEN[unit]))
        self.roll.focus_set()

    def on_gliss_number(self, key, left=False):
        """Copies above / below or Note length box (Enter, stepped, or left: widgets.leave_box)."""
        e, var = self.gliss_nums[key]
        tgts = self.gliss_targets()
        if self._loading or not tgts or left and unchanged(e):
            return
        unit = tgts[0]["gliss"].get("unit", "pct")
        copies = key in ("above", "below")
        try:
            n = float(calc(var.get()))
            ok = (n == int(n) and 0 <= n <= MOST_COPIES) if copies else 0 < n <= MOST_LEN[unit]
            if not math.isfinite(n) or not ok:
                raise ValueError
        except (ValueError, ZeroDivisionError, OverflowError):
            bad(e)
            return
        good(e)
        n = int(n) if copies or n == int(n) else n
        key_ = "gliss_copies" if copies else "gliss_len"
        self.set_gliss(_key=(key_, tuple(sorted(self.sels)), key), **{key: n})

    def picks_text(self, g):
        """The panel's line on the picked keys: "Keys: C E G" (+ single keys), or the hint when none are picked."""
        keys = picked(g)
        full, rest = pick_parts(keys, self.keys - 1)
        if not full and not rest:
            return tr("panel_gliss.no_picks")
        parts = [" ".join(NOTE_NAMES[n] for n in full)] if full else []
        if rest:
            parts.append(trn("panel_gliss.single", len(rest), n=len(rest)))
        return tr("panel_gliss.picks", keys=" + ".join(parts))

    def gliss_picks(self):
        """The picked keys the piano keys light up (the first selected run's / new runs'), or None: not picking."""
        tgts = self.gliss_targets()
        return picked(tgts[0]["gliss"]) if tgts else None

    def gliss_key_click(self, key, one=False):
        """A click on the piano keys while picking: key picked / unpicked (one = Ctrl: that key only, else that note in
        every octave), one undo step per run of clicks; the key sounds (roll.tap_key). False = not picking."""
        tgts = self.gliss_targets()
        if not tgts:
            return False
        keys = pick_key(tgts[0]["gliss"], key, one)
        self.set_gliss(_key=("gliss_keys", tuple(sorted(self.sels))), _snap=False, keys=keys)
        self.roll.request_redraw()
        return True

    def set_gliss(self, _key=None, _snap=True, **changes):
        """Changes the runs' settings (one undo step; _key = its begin_edit key) and the ones new runs start with.
        _snap: their ends snap onto the notes (panel edits, user 11-02); picked keys / dragged copies leave the drawn
        line where it is."""
        tgts = self.gliss_targets()
        if not tgts:
            return
        self.gliss_defaults = dict(self.gliss_defaults, **changes)
        runs = [t for t in tgts if "kind" in t and any(t["gliss"].get(k) != v for k, v in changes.items())]
        if runs:
            self.begin_edit(_key or ("gliss", tuple(sorted(self.sels))))
            for sh in runs:
                sh["gliss"] = dict(sh["gliss"], **changes)
                pts = fit_pts(sh, self.ppq) if _snap else None
                if pts:
                    sh["pts"] = pts
            self.shapes_changed()
            self.sync_points()
        self.sync_gliss()

    def on_gliss_grids(self):
        if not self._loading:
            self.set_gliss(grids=GRID_KINDS[self.gliss_grids.current()])

    def on_gliss_list(self, left=False):
        """The typed grid list (Enter, or left: widgets.leave_box)."""
        if self._loading or left and unchanged(self.gliss_list):
            return
        text = self.gliss_list.get().strip()
        names = read_list(text)
        if not names:
            bad(self.gliss_list)
            return
        good(self.gliss_list)
        self.set_gliss(list=", ".join(names))
