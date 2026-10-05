"""The side panel's funnel settings (wall, inside, gates)."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.funnel import funnel_reversed
from files.mathexpr import calc, fmt
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
    ("change", "", [
        ("steps", tr("panel_funnel.steps"), tr("panel_funnel.the_gate_only_halves_or_doubles")),
        ("smooth", tr("panel_funnel.smooth"), tr("panel_funnel.every_note_gets_its_own_gate"))]),
    ("follow", "", [
        ("time", tr("panel_funnel.evenly"), tr("panel_funnel.the_gate_changes_evenly_from_the")),
        ("curve", tr("panel_funnel.with_the_curve"), tr("panel_funnel.the_gate_changes_as_the_funnel"))]),
]


GATE_STEPS = (1, 10, 1)  # quick changes of a gate in ticks (widgets.Scrub): step, Shift step, Ctrl step


def changed_funnel(t, key, value):
    """Funnel (settings) t with one setting changed; with one gate the wall gate stays the same as it (so turning
    "Different start and wall gate" on starts from there)."""
    out = dict(t, **{key: value})
    if not out["vary"]:
        out["gate1"] = out["gate0"]
    return out


class FunnelPanel:
    """Mixed into App."""

    def _build_funnel(self):
        """Funnel settings: note-off or note-on on the wall, what's inside, the gates."""
        box = self.funnel_box = ttk.Frame(self.settings)
        box.columnconfigure(1, weight=1)
        self.funnel_vars = {key: tk.StringVar() for key, _, _ in FUNNEL_CHOICES}
        self.funnel_entries = {}  # setting -> (variable, entry box)
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

        def entry(row, key, width):
            var = tk.StringVar()
            e = ttk.Entry(row, textvariable=var, width=width)
            e.pack(side="left", padx=(0, 4))
            e.bind("<Return>", lambda ev: self.on_funnel_entry(key))
            self.funnel_entries[key] = (var, e)
            leave_box(self, e, var, lambda left: self.on_funnel_entry(key, left))

        radios(1, "wall")
        radios(2, "fill")
        lb = ttk.Label(box, text=tr("panel_funnel.gate"))
        lb.grid(row=3, column=0, sticky="w")
        row = ttk.Frame(box)
        row.grid(row=3, column=1, sticky="w", padx=(5, 0), pady=1)
        self.funnel_grid["gate"] = (lb, row)
        entry(row, "gate0", 6)
        self.funnel_arrow = ttk.Label(row, text="→")
        self.funnel_arrow.pack(side="left", padx=(0, 4))
        entry(row, "gate1", 6)
        # arrows / wheel step one gate, dragging "Gate" steps both
        gates = [(e, var, lambda key=key: self.on_funnel_entry(key)) for key, (var, e) in self.funnel_entries.items()]
        Scrub(self, gates, GATE_STEPS, 1, 10 ** 7, label=lb)
        self.funnel_ticks = ttk.Label(row, text=tr("unit.ticks"), foreground="#777")
        self.funnel_ticks.pack(side="left")
        self.funnel_gate_tip = Tooltip(row, "")
        self.funnel_vary = tk.BooleanVar()
        vary = ttk.Checkbutton(box, text=tr("panel_funnel.different_start_and_wall_gate"), variable=self.funnel_vary,
                               command=lambda: self.set_funnel("vary", self.funnel_vary.get()))
        vary.grid(row=4, column=1, sticky="w", padx=(5, 0), pady=1)
        self.funnel_grid["vary"] = (vary,)
        Tooltip(vary, tr("panel_funnel.off_one_gate_for_the_whole"))
        radios(5, "change")
        radios(6, "follow")
        self.funnel_info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                                     wraplength=int(300 * self.scale), justify="left")
        self.funnel_info.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(2, 0))

    def new_defaults(self, kind):
        """Settings a new shape of this kind starts with."""
        return dict(self.defaults, **self.funnel_defaults) if kind == "funnel" else self.defaults

    def funnel_targets(self):
        """What the funnel panel changes: the selected funnels, or (with nothing selected) the settings for
        new ones."""
        funnels = [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] == "funnel"]
        return funnels or ([] if self.sels else [self.funnel_defaults])

    def sync_funnel(self, note=None):
        tgts = self.funnel_targets()
        placed = bool(tgts) and tgts[0] is not self.funnel_defaults
        self._rows["funnel"] = placed or bool(tgts and self.tool.get() == "funnel")
        self.layout_rows()
        if not self._rows["funnel"]:
            return
        t = tgts[0]
        self._loading = True
        for key, var in self.funnel_vars.items():
            var.set(t[key])
        for key, (var, e) in self.funnel_entries.items():
            var.set(fmt(round(t[key] * self.ppq, 3)))
            e.config(style="TEntry")
        self.funnel_vary.set(t["vary"])
        self._loading = False
        # a reverse funnel's wall comes first: "one more column" is before it, ending on it
        texts = [tr("panel_funnel.notes_end_on_it"), tr("panel_funnel.notes_start_on_it")]
        if placed and funnel_reversed(t):
            texts.reverse()
        for b, text in zip(self.funnel_radios["wall"], texts):
            b.config(text=text)
        spam, vary, past = t["fill"] == "spam", t["vary"], t["wall"] == "past"
        # long notes: the wall gate is still the length of the column past the wall (with one gate: that one)
        wall_box = self.funnel_entries["gate1"][1]
        self.funnel_entries["gate0"][1].config(state="normal" if spam or (past and not vary) else "disabled")
        wall_box.config(state="normal" if vary and (spam or past) else "disabled")
        # only what does something shows (user): long notes use one gate, and only past the wall (with "different"
        # gates on, the wall one); the wall gate box only when the gates can differ
        start_box = self.funnel_entries["gate0"][1]
        boxes = [(start_box, spam or not vary), (self.funnel_arrow, spam and vary), (wall_box, vary)]
        if [on for _, on in boxes] != [bool(w.winfo_manager()) for w, _ in boxes]:  # (only when it changes: flashes)
            for w, _ in boxes:
                w.pack_forget()
            for w, on in boxes:
                if on:
                    w.pack(side="left", padx=(0, 4), before=self.funnel_ticks)
        for key, on in (("gate", spam or past), ("vary", spam), ("change", spam and vary), ("follow", spam and vary)):
            for w in self.funnel_grid[key]:
                grid_shown(w, on)
        self.funnel_gate_tip.text = (tr("panel_funnel.spam_gate_at_the_start_at") if vary else
                                     tr("panel_funnel.spam_gate_with_long_notes_the"))
        for key in ("change", "follow"):
            for b in self.funnel_radios[key]:
                b.config(state="normal" if spam and vary else "disabled")
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

    def set_funnel(self, key, value):
        """A funnel setting changed in the panel."""
        if self._loading:
            return
        tgts = self.funnel_targets()
        placed = [t for t in tgts if t is not self.funnel_defaults]
        same = all(abs(t[key] - value) < 1e-12 if key in ("gate0", "gate1") else t[key] == value for t in tgts)
        if same or not self.confirm_big([changed_funnel(t, key, value) for t in placed]):
            return self.sync_funnel()
        if placed:
            self.push_undo(name=tr("panel_funnel.funnel_setting"))
        for t in tgts:
            t.update(changed_funnel(t, key, value))
        self.shapes_changed()
        self.sync_funnel()

    def on_funnel_entry(self, key, left=False):
        """A gate box (Enter, stepped, or left: widgets.leave_box)."""
        var, e = self.funnel_entries[key]
        if self._loading or str(e.cget("state")) == "disabled" or left and unchanged(e):
            return
        try:
            ticks = calc(var.get())
            if not 1 <= ticks <= 10 ** 7:
                raise ValueError
            value = ticks / self.ppq
        except ValueError:
            bad(e)
            return
        good(e)
        self.set_funnel(key, value)
