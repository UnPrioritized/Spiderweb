"""The side panel's funnel settings (wall, inside, gates)."""

import tkinter as tk
from tkinter import ttk

from notes.funnel import funnel_reversed
from files.mathexpr import calc, fmt
from window.widgets import Scrub, Tooltip

# Funnel panel: (setting, label, [(value, text, tooltip)])
FUNNEL_CHOICES = [
    ("fill", "Inside", [
        ("spam", "Spam", "Back-to-back notes, lined up in columns on every key."),
        ("long", "Long notes", "One note per key, from where it joins the funnel to the wall.")]),
    # the texts of "wall" swap for a reverse funnel (see sync_funnel)
    ("wall", "Wall", [
        ("in", "Notes end on it", "The notes stop at the wall."),
        ("past", "Notes start on it", "One more column of notes on the other side of the wall,\n"
                                      "as long as the wall gate.")]),
    ("change", "", [
        ("steps", "Steps", "The gate only halves (or doubles): start gate, half of it, a quarter, ...\n"
                           "so the funnel is made of sections of equal notes."),
        ("smooth", "Smooth", "Every note gets its own gate, sliding from the start gate to the wall gate.")]),
    ("follow", "", [
        ("time", "Evenly", "The gate changes evenly from the start to the wall."),
        ("curve", "With the curve", "The gate changes as the funnel opens: it stays near the start gate\n"
                                    "along the line and changes fast where the funnel opens up.")]),
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

        def radios(r, key):
            label, choices = next((lb, ch) for k, lb, ch in FUNNEL_CHOICES if k == key)
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w")
            row = ttk.Frame(box)
            row.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=1)
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
            e.bind("<FocusOut>", lambda ev: self.on_funnel_entry(key))
            self.funnel_entries[key] = (var, e)

        radios(1, "wall")
        radios(2, "fill")
        lb = ttk.Label(box, text="Gate")
        lb.grid(row=3, column=0, sticky="w")
        row = ttk.Frame(box)
        row.grid(row=3, column=1, sticky="w", padx=(5, 0), pady=1)
        entry(row, "gate0", 6)
        self.funnel_arrow = ttk.Label(row, text="→")
        self.funnel_arrow.pack(side="left", padx=(0, 4))
        entry(row, "gate1", 6)
        # arrows / wheel step one gate, dragging "Gate" steps both
        gates = [(e, var, lambda key=key: self.on_funnel_entry(key)) for key, (var, e) in self.funnel_entries.items()]
        Scrub(self, gates, GATE_STEPS, 1, 10 ** 7, label=lb)
        self.funnel_ticks = ttk.Label(row, text="ticks", foreground="#777")
        self.funnel_ticks.pack(side="left")
        self.funnel_gate_tip = Tooltip(row, "")
        self.funnel_vary = tk.BooleanVar()
        vary = ttk.Checkbutton(box, text="Different start and wall gate", variable=self.funnel_vary,
                               command=lambda: self.set_funnel("vary", self.funnel_vary.get()))
        vary.grid(row=4, column=1, sticky="w", padx=(5, 0), pady=1)
        Tooltip(vary, "Off: one gate for the whole funnel.\n"
                      "On: the gate changes from the start gate to the wall gate (Steps / Smooth, Evenly / With the "
                      "curve say how).")
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
        texts = ["Notes end on it", "Notes start on it"]
        if placed and funnel_reversed(t):
            texts.reverse()
        for b, text in zip(self.funnel_radios["wall"], texts):
            b.config(text=text)
        spam, vary, past = t["fill"] == "spam", t["vary"], t["wall"] == "past"
        # long notes: the wall gate is still the length of the column past the wall (with one gate: that one)
        wall_box = self.funnel_entries["gate1"][1]
        self.funnel_entries["gate0"][1].config(state="normal" if spam or (past and not vary) else "disabled")
        wall_box.config(state="normal" if vary and (spam or past) else "disabled")
        if vary:  # the wall gate box shows only when the gates can differ
            self.funnel_arrow.pack(side="left", padx=(0, 4), before=self.funnel_ticks)
            wall_box.pack(side="left", padx=(0, 4), before=self.funnel_ticks)
        else:
            self.funnel_arrow.pack_forget()
            wall_box.pack_forget()
        self.funnel_gate_tip.text = ("Spam gate at the start → at the wall. Enter to apply." if vary else
                                     "Spam gate (with long notes: the column past the wall). Enter to apply.")
        for key in ("change", "follow"):
            for b in self.funnel_radios[key]:
                b.config(state="normal" if spam and vary else "disabled")
        draft = self.roll.draft
        if draft and draft["kind"] == "funnel" and len(draft["pts"]) == 2:
            info = (note + " " if note else "") + "Now draw the wall (Ctrl = centred on the line, right-click = cancel)."
        elif placed:
            info = f"{sum(self.note_count(x) for x in tgts):,} notes.  "
            parts = self.roll.parts_text()
            if parts:
                info += (f"Highlighted: {parts}. Right-click = curve shapes, Del = delete, Esc = clear. "
                         "Ctrl+click = one more / one less.")
            elif len(tgts) == 1 and not t["starts"]:
                info += "Middle-click on the line to start a curve there.  A line drawn to its wall = one more line."
            else:
                info += "Middle-click the line = new curve, near a curve = anchor. Select tool: click a curve again = highlight."
        else:
            info = "Draw the funnel's line, then its wall (drag, or click twice each)."
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
            self.push_undo(name="Funnel setting")
        for t in tgts:
            t.update(changed_funnel(t, key, value))
        self.shapes_changed()
        self.sync_funnel()

    def on_funnel_entry(self, key):
        var, e = self.funnel_entries[key]
        if self._loading or str(e.cget("state")) == "disabled":
            return
        try:
            ticks = calc(var.get())
            if not 1 <= ticks <= 10 ** 7:
                raise ValueError
            value = ticks / self.ppq
        except ValueError:
            e.config(style="Bad.TEntry")
            return
        e.config(style="TEntry")
        self.set_funnel(key, value)
