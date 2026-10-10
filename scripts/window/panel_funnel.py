"""The side panel's funnel settings (wall, inside, gate + its Range)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.funnel import funnel_reversed, gate_ticks
from notes.hzbass import HZ_DEFAULTS, shortest_gate
from files.mathexpr import calc
from window import look
from window.widgets import Scrub, Tooltip, bad, good, grid_shown, leave_box, unchanged

# Funnel panel: (setting, label, [(value, text, tooltip)])
FUNNEL_CHOICES = [
    ("fill", tr("panel_funnel.inside"), [
        ("spam", tr("panel_funnel.spam"), tr("panel_funnel.back_to_back_notes_lined_up")),
        ("long", tr("panel_funnel.long_notes"), tr("panel_funnel.one_note_per_key_from_where"))]),
    # the texts of "wall" swap for a reverse funnel (see sync_funnel)
    ("wall", tr("panel_funnel.wall"), [
        ("in", tr("panel_funnel.notes_end_on_it"), tr("panel_funnel.the_notes_stop_at_the_wall")),
        ("past", tr("panel_funnel.notes_start_on_it"), tr("panel_funnel.one_more_column_of_notes_on"))]),
]


GATE_STEPS = (1, 10, 1)  # quick changes of a gate in ticks (widgets.Scrub): step, Shift step, Ctrl step


def changed_funnel(t, key, value):
    """Funnel (settings) t with one setting changed; a new gate typed takes its Range off (kept for the Range window
    to bring back, like a custom shape's)."""
    out = dict(t, **{key: value})
    if key == "gate" and out.get("range"):
        out["range_kept"] = out.pop("range")
    return out


class FunnelPanel:
    """Mixed into App."""

    def _build_funnel(self):
        """Funnel settings: note-off or note-on on the wall, what's inside, the gate, Hz bass."""
        from window.hz_window import open_hz
        from window.range_window import open_funnel_range
        box = self.funnel_box = ttk.Frame(self.settings)
        box.columnconfigure(1, weight=1)
        self.funnel_vars = {key: tk.StringVar() for key, _, _ in FUNNEL_CHOICES}
        self.funnel_radios = {}   # setting -> [radio buttons]
        self.funnel_grid = {}     # row name -> its widgets in the grid (hidden while they do nothing, user)

        def radios(r, key):
            label, choices = next((lb, ch) for k, lb, ch in FUNNEL_CHOICES if k == key)
            lb = ttk.Label(box, text=label)
            lb.grid(row=r, column=0, sticky="w")
            row = ttk.Frame(box)
            row.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=1)
            self.funnel_grid[key] = (lb, row)
            self.funnel_radios[key] = []
            for value, text, tip in choices:
                b = ttk.Radiobutton(row, text=text, value=value, variable=self.funnel_vars[key],
                                    command=lambda key=key: self.set_funnel(key, self.funnel_vars[key].get()))
                b.pack(side="left", padx=(0, 6))
                Tooltip(b, tip)
                self.funnel_radios[key].append(b)

        radios(1, "wall")
        radios(2, "fill")
        lb = ttk.Label(box, text=tr("panel_funnel.gate"))
        lb.grid(row=3, column=0, sticky="w")
        row = ttk.Frame(box)
        row.grid(row=3, column=1, sticky="w", padx=(5, 0), pady=1)
        self.funnel_grid["gate"] = (lb, row)
        var = self.funnel_gate_var = tk.StringVar()
        e = self.funnel_gate_entry = ttk.Entry(row, textvariable=var, width=6)
        e.pack(side="left", padx=(0, 4))
        e.bind("<Return>", lambda ev: self.on_funnel_entry())
        leave_box(self, e, var, lambda left: self.on_funnel_entry(left))
        Scrub(self, [(e, var, self.on_funnel_entry)], GATE_STEPS, 1, 10 ** 7, label=lb)
        ttk.Label(row, text=tr("unit.ticks"), foreground=look.HINT).pack(side="left")
        self.funnel_gate_tip = Tooltip(e, "")
        # Range (gaterange.py, the same window as a custom shape's): the gate goes from this one at the line start
        # to a second one at the wall, along a graph, smoothly or in steps, evenly or with the curve
        self.funnel_range_btn = ttk.Button(row, text=tr("panel_custom.range"),
                                           command=lambda: open_funnel_range(self))
        self.funnel_range_btn.pack(side="left", padx=(8, 0))
        Tooltip(self.funnel_range_btn, tr("panel_funnel.range_tip"))
        # Hz bass (hzbass.py): the whole funnel is one (user). The same switch, Notes… and warnings as a custom
        # shape's; new funnels never start with it (greyed until one is placed)
        row = ttk.Frame(box)
        row.grid(row=4, column=0, columnspan=2, sticky="w", pady=1)
        self.funnel_hz_var = tk.BooleanVar()
        self.funnel_hz_check = ttk.Checkbutton(row, text=tr("panel_custom.hz_bass"), variable=self.funnel_hz_var,
                                               command=self.on_funnel_hz)
        self.funnel_hz_check.pack(side="left")
        Tooltip(self.funnel_hz_check, tr("panel_funnel.hz_tip"))
        b = self.funnel_hz_notes = ttk.Button(row, text=tr("panel_custom.hz_notes"), command=lambda: open_hz(self))
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, tr("panel_custom.hz_notes_tip"))
        self.funnel_hz_info = ttk.Label(box, text="", foreground=look.WARN, font=look.font(8),
                                        wraplength=int(300 * self.scale), justify="left")  # (short gates)
        self.funnel_hz_info.grid(row=5, column=0, columnspan=2, sticky="w")
        self.funnel_hz_stale = ttk.Frame(box)  # the BPM changed since: its tone is off until it's updated
        ttk.Label(self.funnel_hz_stale, text=tr("panel_custom.hz_stale"), foreground=look.WARN, font=look.font(8),
                  wraplength=int(300 * self.scale), justify="left").pack(anchor="w")
        ttk.Button(self.funnel_hz_stale, text=tr("panel_custom.hz_update"),
                   command=self.update_hz).pack(anchor="w", pady=(1, 2))
        self.funnel_hz_stale.grid(row=6, column=0, columnspan=2, sticky="w")
        self.funnel_info = ttk.Label(box, text="", foreground=look.HINT, font=look.font(8),
                                     wraplength=int(300 * self.scale), justify="left")
        self.funnel_info.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(2, 0))

    def new_defaults(self, kind):
        """Settings a new shape of this kind starts with."""
        return dict(self.defaults, **self.funnel_defaults) if kind == "funnel" else self.defaults

    def funnel_targets(self):
        """What the funnel panel changes: the selected funnels, or (with nothing selected) the settings for
        new ones. A funnel being drawn: the settings for new ones (it takes them too, see set_funnel)."""
        if self.funnel_draft():
            return [self.funnel_defaults]
        funnels = [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] == "funnel"]
        return funnels or ([] if self.sels else [self.funnel_defaults])

    def funnel_draft(self):
        """The funnel being drawn (its line drawn, waiting for the wall), or None."""
        draft = self.roll.draft
        return draft if draft and draft["kind"] == "funnel" and len(draft["pts"]) == 2 else None

    def sync_funnel(self, note=None):
        tgts = self.funnel_targets()
        placed = bool(tgts) and tgts[0] is not self.funnel_defaults
        self._rows["funnel"] = placed or bool(tgts and self.tool.get() == "funnel")
        self.layout_rows()
        if not self._rows["funnel"]:
            return
        t = tgts[0]
        spam, past = t["fill"] == "spam", t["wall"] == "past"
        rg = t.get("range") if spam else None  # (long notes: one gate, the column past the wall)
        self._loading = True
        for key, var in self.funnel_vars.items():
            var.set(t[key])
        self.funnel_gate_var.set(str(gate_ticks(t["gate"], self.ppq)))  # (the ticks used)
        self.funnel_gate_entry.config(style="Gap.TEntry" if rg else "TEntry")
        self._loading = False
        # a reverse funnel's wall comes first: "one more column" is before it, ending on it
        texts = [tr("panel_funnel.notes_end_on_it"), tr("panel_funnel.notes_start_on_it")]
        if placed and funnel_reversed(t):
            texts.reverse()
        for b, text in zip(self.funnel_radios["wall"], texts):
            b.config(text=text)
        # only what does something shows (user): long notes use the gate only past the wall, and no Range; a Hz bass
        # makes its own notes from its tones (no Inside, no gate)
        hz = t.get("hz") if placed else None
        for w in self.funnel_grid["fill"]:
            grid_shown(w, not hz)
        for w in self.funnel_grid["gate"]:
            grid_shown(w, (spam or past) and not hz)
        self.sync_funnel_hz(tgts, hz, placed)
        if spam != bool(self.funnel_range_btn.winfo_manager()):  # (only when it changes: flashes)
            if spam:
                self.funnel_range_btn.pack(side="left", padx=(8, 0))
            else:
                self.funnel_range_btn.pack_forget()
        # (the funnel being drawn takes the panel's settings, but the Range window works on placed ones)
        self.funnel_range_btn.config(state="disabled" if self.funnel_draft() else "normal")
        self.funnel_gate_tip.text = (tr("panel_funnel.gate_ranged_tip", a=gate_ticks(t["gate"], self.ppq),
                                        b=gate_ticks(rg["to"], self.ppq)) if rg else
                                     tr("panel_funnel.spam_gate_with_long_notes_the"))
        draft = self.roll.draft
        if draft and draft["kind"] == "funnel" and len(draft["pts"]) == 2:
            info = (note + " " if note else "") + tr("panel_funnel.now_draw_the_wall_ctrl_centred")
        elif placed:
            info = tr("panel_funnel.notes", value=sum(self.note_count(x) for x in tgts))
            parts = self.roll.parts_text()
            if parts:
                info += (tr("panel_funnel.highlighted_right_click_curve_shapes_del", parts=parts))
            elif len(tgts) == 1 and not t["starts"]:
                info += tr("panel_funnel.middle_click_on_the_line_to")
            else:
                info += tr("panel_funnel.middle_click_the_line_new_curve")
        else:
            info = tr("panel_funnel.draw_the_funnel_s_line_then")
        self.funnel_info.config(text=info)

    def sync_funnel_hz(self, tgts, hz, placed):
        """The Hz bass row: on / off (greyed for new funnels: they never start with it), the warnings (short gates,
        BPM changed)."""
        from window.panel_custom import HZ_SHORT
        self._loading = True
        self.funnel_hz_var.set(bool(hz))
        self._loading = False
        for w in (self.funnel_hz_check, self.funnel_hz_notes):
            w.config(state="normal" if placed else "disabled")
        bpm = self.current_bpm()
        stale = bool(hz) and bpm is not None and any(t.get("hz") and abs(t["hz"]["bpm"] - bpm) > 1e-9 for t in tgts)
        short = bool(hz) and shortest_gate(dict(hz, bpm=bpm or hz["bpm"]), self.ppq) < HZ_SHORT
        if short:
            self.funnel_hz_info.config(text=tr("panel_custom.hz_short_fixed" if hz.get("fixed")
                                               else "panel_custom.hz_short").strip())
        grid_shown(self.funnel_hz_info, short)
        grid_shown(self.funnel_hz_stale, stale)

    def on_funnel_hz(self):
        """The funnel panel's Hz bass box ticked or cleared: the selected funnels."""
        if self._loading:
            return
        tgts = [t for t in self.funnel_targets() if t is not self.funnel_defaults]
        if tgts:
            self.set_hz({k: HZ_DEFAULTS[k] for k in ("key", "cents")} if self.funnel_hz_var.get() else None, tgts)

    def set_funnel(self, key, value):
        """A funnel setting changed in the panel."""
        if self._loading:
            return
        tgts = self.funnel_targets()
        placed = [t for t in tgts if t is not self.funnel_defaults]
        same = all(abs(t[key] - value) < 1e-12 if key == "gate" else t[key] == value for t in tgts)
        if same or not self.confirm_big([changed_funnel(t, key, value) for t in placed]):
            return self.sync_funnel()
        if placed:
            self.push_undo(name=tr("panel_funnel.funnel_setting"))
        for t in tgts + [d for d in (self.funnel_draft(),) if d]:  # (the funnel being drawn too: user)
            new = changed_funnel(t, key, value)
            t.clear()
            t.update(new)
        self.shapes_changed()
        self.sync_funnel()

    def on_funnel_entry(self, left=False):
        """The gate box (Enter, stepped, or left: widgets.leave_box). Whole ticks: a fraction is rounded (user)."""
        var, e = self.funnel_gate_var, self.funnel_gate_entry
        if self._loading or str(e.cget("state")) == "disabled" or left and unchanged(e):
            return
        try:
            ticks = calc(var.get())
            if not 0.5 <= ticks <= 10 ** 7:
                raise ValueError
            value = math.floor(ticks + 0.5) / self.ppq
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        self.set_funnel("gate", value)
        good(e)  # (after: the box shows the whole ticks now)
