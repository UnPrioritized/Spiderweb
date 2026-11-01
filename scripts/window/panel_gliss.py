"""The side panel's Glissando settings (notes/gliss.py): which grids a run's step picks from, and what it picked."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from notes.gliss import DEFAULTS, GRID_KINDS, fit_pts, pick_key, pick_parts, picked, plan, read_list
from roll.roll_shared import NOTE_NAMES
from window import look
from window.widgets import Tooltip, bad, good, leave_box, unchanged


class GlissPanel:
    """Mixed into App."""

    def _build_gliss(self):
        box = self.gliss_box = ttk.Frame(self.settings)
        row = ttk.Frame(box)
        row.pack(fill="x")
        lb = ttk.Label(row, text=tr("panel_gliss.grids"))
        lb.pack(side="left")
        cb = self.gliss_grids = ttk.Combobox(row, state="readonly", width=18,
                                              values=(tr("panel_gliss.halves"), tr("panel_gliss.my_list")))
        cb.pack(side="left", padx=(5, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_gliss_grids())
        for w in (lb, cb):
            Tooltip(w, tr("panel_gliss.grids_tip"))
        self.gliss_var = tk.StringVar()
        e = self.gliss_list = ttk.Entry(box, textvariable=self.gliss_var, width=24)
        e.bind("<Return>", lambda ev: self.on_gliss_list())
        leave_box(self, e, self.gliss_var, self.on_gliss_list)
        Tooltip(e, tr("panel_gliss.list_tip"))
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
            self.gliss_list.pack(fill="x", pady=(3, 0), before=self.gliss_info)
        else:
            self.gliss_list.pack_forget()
        self.gliss_var.set(g.get("list", DEFAULTS["list"]))
        good(self.gliss_list)
        self._loading = False
        lines = [self.picks_text(g)]
        if "kind" in tgts[0]:  # a run: the step it picked and how many keys
            _, _, name, keys = plan(tgts[0], self.ppq)
            lines.insert(0, tr("panel_gliss.info", grid=name, keys=trn("n.keys", len(keys), n=len(keys))))
        self.gliss_info.config(text="\n".join(lines))

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
        self.set_gliss(_key=("gliss_keys", tuple(sorted(self.sels))), keys=keys)
        self.roll.request_redraw()
        return True

    def set_gliss(self, _key=None, **changes):
        """Changes the runs' settings (one undo step; _key = its begin_edit key) and the ones new runs start with;
        their ends snap again."""
        tgts = self.gliss_targets()
        if not tgts:
            return
        self.gliss_defaults = dict(self.gliss_defaults, **changes)
        runs = [t for t in tgts if "kind" in t and any(t["gliss"].get(k) != v for k, v in changes.items())]
        if runs:
            self.begin_edit(_key or ("gliss", tuple(sorted(self.sels))))
            for sh in runs:
                sh["gliss"] = dict(sh["gliss"], **changes)
                pts = fit_pts(sh, self.ppq)
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
