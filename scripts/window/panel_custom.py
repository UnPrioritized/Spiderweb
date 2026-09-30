"""The side panel's custom shape settings (which shape, how it's filled) and the drawer window."""

import copy
import math
import re
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from files.lang import tr
from notes.custom import (CUSTOM_FLAGS, ENDS, HZ_DEFAULTS, SPAM_FILLS, box_frame, custom_settings, gap_lines, hz_gate,
                          hz_of, join_strokes, map_stroke, normalize_strokes, open_paths)
from window.drawer import Drawer, clean_name, library_names, load_shape, save_shape
from window.panel_funnel import GATE_STEPS
from files.mathexpr import calc, fmt
from roll.roll_live import BOX_TOOLS, STROKE_TOOLS
from roll.roll_shared import NOTE_NAMES, note_name
from window.widgets import Scrub, Tooltip

GAP_COLOR = "#c06000"  # Fill / Spam on a shape whose outline has one gap (closed with a straight line)
FILL_CHOICES = [
    ("empty", tr("panel_custom.empty"), tr("panel_custom.just_the_outline_like_lines")),
    ("fill", tr("panel_custom.fill"), tr("panel_custom.one_long_note_per_key_inside")),
    ("spam", tr("panel_custom.spam"), tr("panel_custom.the_inside_filled_with_back_to")),
    ("outline_spam", tr("panel_custom.outline_spam"), tr("panel_custom.just_the_outline_chopped_into_notes")),
]
CUSTOM_NAMES = {"fill": tr("panel_custom.inside_fill"), "gate": tr("panel_custom.spam_gate"),
                "align": tr("panel_custom.spam_start"), "ends": tr("panel_custom.spam_ends"),
                "union": tr("panel_custom.overlaps_cancel_out"),
                "apart": tr("panel_custom.normal_outline")}  # (History)
APART_CHOICES = [tr("panel_custom.normal"), tr("panel_custom.outline")]
APART_TIP = tr("panel_custom.normal_all_its_notes_together_outline")
APART_NEEDS = tr("panel_custom.channels_isn_t_multi_channel_now")
CANCEL_TIP = tr("panel_custom.fill_and_spam_where_outlines_overlap")
ALIGN_CHOICES = [
    ("auto", tr("panel_custom.auto"), tr("panel_custom.each_key_s_notes_start_at")),
    ("aligned", tr("panel_custom.aligned"), tr("panel_custom.every_note_sits_on_the_gate")),
    ("centred", tr("panel_custom.centred"), tr("panel_custom.centred_tip")),
]
END_CHOICES = [(value, tr("panel_custom.ends_" + value)) for value in ENDS]
HZ_SHORT = 40  # Hz bass: a gate under this many ticks wobbles between its two sizes (orange: a higher PPQ fixes it)


def tone_key(text):
    """'A1', 'c#2', 'Bb0' or a key number -> the key (C4 = 60). ValueError if it isn't one."""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", text.strip())
    if m:
        key = NOTE_NAMES.index(m[1].upper()) + {"#": 1, "b": -1, "": 0}[m[2]] + (int(m[3]) + 1) * 12
    else:
        key = int(text)
    if not 0 <= key <= 127:
        raise ValueError
    return key


class CustomPanel:
    """Mixed into App."""

    def _build_custom(self):
        """Custom shape settings: which library shape, and how the inside is filled."""
        box = self.custom_box = ttk.Frame(self.settings)
        row = self.custom_shape_row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text=tr("panel_custom.shape")).pack(side="left")
        self.custom_combo = ttk.Combobox(row, textvariable=self.custom_pick, state="readonly", width=18,
                                         postcommand=self.refresh_custom_names)
        self.custom_combo.pack(side="left", padx=(5, 0))
        self.custom_combo.bind("<<ComboboxSelected>>", lambda e: self.on_custom_pick())
        ttk.Button(row, text=tr("panel_custom.drawer"), command=self.open_drawer).pack(side="left", padx=(4, 0))
        row = self.custom_fill_row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text=tr("panel_custom.inside")).pack(side="left", anchor="n")
        opts = ttk.Frame(row)
        opts.pack(side="left", padx=(5, 0))
        self.fill_buttons = {}
        self.fill_tips = {}  # (Fill / Spam also say why they're orange (one gap) or greyed out (more))
        ttk.Style(self).configure("Gap.TRadiobutton", foreground=GAP_COLOR)
        ttk.Style(self).configure("Gap.TCombobox", foreground=GAP_COLOR)
        self.apart_var = tk.StringVar(value=APART_CHOICES[0])
        self.apart_boxes, self.apart_tips = {}, {}  # Fill / Spam: Normal, or Outline (on a channel of its own)
        rows = ttk.Frame(opts)  # (a grid, so the Fill and Spam dropdowns line up)
        rows.pack(anchor="w", fill="x")
        for row, (value, text, tip) in enumerate(FILL_CHOICES):
            b = ttk.Radiobutton(rows, text=text, value=value, variable=self.fill_var,
                                command=lambda: self.set_custom("fill", self.fill_var.get()))
            b.grid(row=row, column=0, sticky="w", columnspan=1 if value in ("fill", "spam") else 2)
            self.fill_buttons[value] = b
            self.fill_tips[value] = Tooltip(b, tip)
            if value in ("fill", "spam"):
                drop = ttk.Combobox(rows, textvariable=self.apart_var, values=APART_CHOICES, state="readonly",
                                    width=8)
                drop.grid(row=row, column=1, sticky="w", padx=(14, 0))
                drop.bind("<<ComboboxSelected>>", lambda e: (
                    self.set_custom("apart", self.apart_var.get() == APART_CHOICES[1]), self.roll.focus_set()))
                self.apart_boxes[value], self.apart_tips[value] = drop, Tooltip(drop, APART_TIP)
        g = ttk.Frame(opts)
        g.pack(anchor="w", padx=(20, 0))
        lb = ttk.Label(g, text=tr("panel_custom.gate"))
        lb.pack(side="left")
        self.gate_entry = ttk.Entry(g, textvariable=self.gate_var, width=7)
        self.gate_entry.pack(side="left", padx=4)
        ttk.Label(g, text=tr("panel_custom.ticks_enter_to_apply"), foreground="#777").pack(side="left")
        self.gate_entry.bind("<Return>", lambda e: self.on_gate())
        self.gate_entry.bind("<FocusOut>", lambda e: self.on_gate())
        Scrub(self, [(self.gate_entry, self.gate_var, self.on_gate)], GATE_STEPS, 1, 10 ** 7, label=lb)
        # Hz bass: the gate is one wave of a tone (custom.py)
        h = self.hz_row = ttk.Frame(opts)
        h.pack(anchor="w", padx=(20, 0), pady=(1, 0))
        self.hz_var, self.hz_tone, self.hz_cents = tk.BooleanVar(), tk.StringVar(), tk.StringVar(value="0")
        self.hz_check = ttk.Checkbutton(h, text=tr("panel_custom.hz_bass"), variable=self.hz_var, command=self.on_hz)
        self.hz_check.pack(side="left")
        Tooltip(self.hz_check, tr("panel_custom.hz_tip"))
        self.hz_tone_entry = ttk.Entry(h, textvariable=self.hz_tone, width=5)
        self.hz_tone_entry.pack(side="left", padx=(4, 0))
        Tooltip(self.hz_tone_entry, tr("panel_custom.hz_tone_tip"))
        lb = ttk.Label(h, text=tr("panel_custom.hz_pitch"))
        lb.pack(side="left", padx=(6, 0))
        self.hz_cents_entry = ttk.Entry(h, textvariable=self.hz_cents, width=5)
        self.hz_cents_entry.pack(side="left", padx=4)
        Tooltip(self.hz_cents_entry, tr("panel_custom.hz_cents_tip"))
        ttk.Label(h, text=tr("panel_custom.hz_cents"), foreground="#777").pack(side="left")
        for entry in (self.hz_tone_entry, self.hz_cents_entry):
            entry.bind("<Return>", lambda ev: self.on_hz_entry())
            entry.bind("<FocusOut>", lambda ev: self.on_hz_entry())
        Scrub(self, [(self.hz_cents_entry, self.hz_cents, self.on_hz_entry)], (1, 10, 0.1), -1200, 1200, label=lb)
        self.hz_info = ttk.Label(opts, text="", foreground="#777", font=("Segoe UI", 8),
                                 wraplength=int(270 * self.scale), justify="left")
        self.hz_stale = ttk.Frame(opts)  # the BPM changed since: its tone is off until it's updated
        ttk.Label(self.hz_stale, text=tr("panel_custom.hz_stale"), foreground=GAP_COLOR, font=("Segoe UI", 8),
                  wraplength=int(270 * self.scale), justify="left").pack(anchor="w")
        ttk.Button(self.hz_stale, text=tr("panel_custom.hz_update"), command=self.update_hz).pack(anchor="w", pady=(1, 2))
        e = ttk.Frame(opts)
        e.pack(anchor="w", padx=(20, 0), pady=(1, 0))
        ttk.Label(e, text=tr("panel_custom.ends")).pack(side="left")
        self.ends_var = tk.StringVar(value=END_CHOICES[0][1])
        self.ends_box = ttk.Combobox(e, textvariable=self.ends_var, values=[t for _, t in END_CHOICES],
                                     state="readonly", width=15)
        self.ends_box.pack(side="left", padx=4)
        self.ends_box.bind("<<ComboboxSelected>>", lambda ev: (
            self.set_custom("ends", ENDS[self.ends_box.current()]), self.roll.focus_set()))
        Tooltip(self.ends_box, tr("panel_custom.ends_tip"))
        a = ttk.Frame(opts)
        a.pack(anchor="w", padx=(20, 0), pady=(1, 0))
        ttk.Label(a, text=tr("panel_custom.start")).pack(side="left")
        self.align_buttons = []
        for value, text, tip in ALIGN_CHOICES:
            b = ttk.Radiobutton(a, text=text, value=value, variable=self.align_var,
                                command=lambda: self.set_custom("align", self.align_var.get()))
            b.pack(side="left", padx=(4, 0))
            Tooltip(b, tip)
            self.align_buttons.append(b)
        self.cancel_var = tk.BooleanVar(value=True)
        self.cancel_box = ttk.Checkbutton(opts, text=tr("panel_custom.overlaps_cancel_out"), variable=self.cancel_var,
                                          command=lambda: self.set_custom("union", not self.cancel_var.get()))
        self.cancel_box.pack(anchor="w", pady=(2, 0))
        Tooltip(self.cancel_box, CANCEL_TIP)
        self.custom_info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                                     wraplength=int(300 * self.scale), justify="left")
        self.custom_info.pack(fill="x", pady=(2, 0))

    def refresh_custom_names(self):
        self.custom_combo.config(values=library_names())

    def custom_template(self, name):
        """A library shape ready to place: (strokes filling the 0..1 box, width/height as drawn), or None."""
        strokes = load_shape(name) if name else None
        return normalize_strokes(strokes) if strokes else None

    def new_custom(self, strokes, b0, p0, b1, p1):
        return dict(self.defaults, kind="custom", name=self.custom_shape, strokes=copy.deepcopy(strokes),
                    **custom_settings(self.custom_defaults), pts=box_frame(b0, p0, b1, p1))

    def custom_targets(self):
        """What the custom shape panel changes: the selected custom shapes, or (with nothing selected) the
        settings for new ones."""
        customs = [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] == "custom"]
        return customs or ([] if self.sels else [self.custom_defaults])

    def sync_custom(self):
        tgts = self.custom_targets()
        placed = bool(tgts) and tgts[0] is not self.custom_defaults
        tool = self.tool.get()
        # the tools that make custom shapes: Custom shape, Circle, Polygon, and with Live shape on the drawing tools
        live = self.live.get() and tool in STROKE_TOOLS
        makes_custom = tool in ("custom", "text") or tool in BOX_TOOLS or live
        # "last note" means nothing for custom shapes and funnels
        kinds = {self.shapes[i]["kind"] for i in self.sels} or {"custom" if makes_custom else tool}
        self._rows["last"] = not kinds <= {"custom", "funnel"}
        self._rows["custom"] = placed or bool(tgts and makes_custom)
        self.layout_rows()
        if not self._rows["custom"]:
            return
        self.sync_polygon()
        # text: no library shape to pick (its letters are the shape)
        text = all(t.get("text") or "notes" in t for t in tgts) if placed else tool == "text"
        # pasted notes: nothing to fill either (the notes are the shape)
        pasted = placed and all("notes" in t for t in tgts)
        if not self.custom_fill_row.winfo_manager():
            self.custom_fill_row.pack(fill="x", before=self.custom_info)
        if text and self.custom_shape_row.winfo_manager():
            self.custom_shape_row.pack_forget()
            self.custom_fill_row.pack_configure(pady=0)
        elif not text and not self.custom_shape_row.winfo_manager():
            self.custom_shape_row.pack(fill="x", before=self.custom_fill_row)
            self.custom_fill_row.pack_configure(pady=(4, 0))
        if pasted:
            self.custom_fill_row.pack_forget()
            own = all(t.get("own_vel") for t in tgts)
            self.custom_info.config(text=(
                tr("panel_custom.pasted_notes", value=sum(self.note_count(t) for t in tgts))
                + (tr("panel_custom.they_keep_their_own_velocities_until")
                   if own else "")
                + tr("panel_custom.drag_a_corner_or_side_to")))
            return
        # gaps in the outline: Fill / Spam close them with straight lines (custom.fill_plan)
        if placed:
            name, gaps = tgts[0]["name"], max(len(gap_lines(t)) for t in tgts)
        elif tool != "custom":  # drawn on the roll: the fill settings are for what gets drawn
            name, gaps = tr("custom.live_drawing") if live else tool.title(), 0
        else:
            name, tpl = self.custom_shape, self.custom_template(self.custom_shape)
            gaps = len(open_paths(tpl[0])) if tpl else 0
        fill, gate = tgts[0]["fill"], tgts[0]["gate"]
        self._loading = True
        self.custom_pick.set(name)
        self.fill_var.set(fill)
        self.gate_var.set(fmt(round(gate * self.ppq, 3)))
        self.align_var.set(tgts[0].get("align", "auto"))
        ends = tgts[0].get("ends", "drop")
        self.ends_box.current(ENDS.index(ends) if ends in ENDS else 0)
        self.gate_entry.config(style="TEntry")
        self._loading = False
        for value, b in self.fill_buttons.items():
            b.config(style="Gap.TRadiobutton" if gaps and value in ("fill", "spam") else "TRadiobutton")
        for value, _, base in FILL_CHOICES:
            gap = (tr("panel_custom.the_outline_has_a_gap_it")
                   if gaps == 1 else
                   tr("panel_custom.the_outline_has_gaps_each_is", gaps=gaps)
                   if gaps else "")
            if gap:
                gap += tr("panel_custom.ends_that_nearly_touch_1_64")
            self.fill_tips[value].text = base + ("\n\n" + gap if gap and value in ("fill", "spam") else "")
        spam = fill in SPAM_FILLS
        hz = self.sync_hz(tgts, spam)  # (Hz bass: its own gate and grid, so gate / ends / start are off)
        self.gate_entry.config(state="normal" if spam and not hz else "disabled")
        self.ends_box.config(state="readonly" if spam and not hz else "disabled")
        for b in self.align_buttons:  # (stretched gates fill each key exactly: where they start doesn't matter)
            b.config(state="normal" if spam and not hz and ends != "stretch" else "disabled")
        self._loading = True
        self.cancel_var.set(not tgts[0].get("union"))
        apart = bool(tgts[0].get("apart"))
        self.apart_var.set(APART_CHOICES[apart])
        self._loading = False
        lonely = apart and self.channel_mode.get() != "auto"  # (Outline without Multi channel: all one channel)
        for value, box in self.apart_boxes.items():
            box.config(state="readonly" if fill == value else "disabled",
                       style="Gap.TCombobox" if lonely and fill == value else "TCombobox")
            self.apart_tips[value].text = APART_TIP + (APART_NEEDS if lonely else "")
        self.cancel_box.config(state="normal" if fill in ("fill", "spam") and not text else "disabled")
        if not placed and live:
            info = tr("panel_custom.live_shape_what_you_draw_goes")
        elif not placed and tool == "text":
            info = tr("panel_custom.inside_fill_for_new_text")
        elif not placed and tool in BOX_TOOLS:
            info = tr("panel_custom.drag_a_box_on_the_piano", tool=tool)
        elif not placed and not self.custom_template(name):
            info = tr("panel_custom.pick_a_shape_or_make_one")
        elif placed:
            info = tr("panel_custom.n_notes", n=sum(self.note_count(t) for t in tgts))
            if gaps and fill in ("fill", "spam"):
                info += (tr("panel_custom.one_gap_in_the_outline_filled") if gaps == 1 else
                         tr("panel_custom.gaps_in_the_outline_filled_as", gaps=gaps))
        else:
            info = tr("panel_custom.drag_a_box_on_the_piano_2")
        if placed and tool != "text":
            info += tr("panel_custom.drag_a_corner_or_side_to_2")
            if live and len(tgts) == 1:
                info += tr("panel_custom.live_shape_what_you_draw_now")
            if self.stroke is not None and len(tgts) == 1:
                info += (tr("panel_custom.picked_stroke_of_del_delete_it", stroke=self.stroke + 1,
                            n=len(tgts[0]['strokes'])))
        self.custom_info.config(text=info)

    def on_custom_pick(self):
        """A shape picked in the box: used for new custom shapes, and swapped into the selected ones."""
        name = self.custom_pick.get()
        tpl = self.custom_template(name)
        if not tpl:
            messagebox.showerror(tr("panel_custom.spiderweb"), tr("panel_custom.couldn_t_read_the_shape", name=name))
            return self.sync_custom()
        self.custom_shape = name
        tgts = [t for t in self.custom_targets()
                if t is not self.custom_defaults and not t.get("text") and "notes" not in t]
        if tgts:
            self.push_undo(name=tr("panel_custom.custom_shape"))
            for t in tgts:
                t["name"], t["strokes"] = name, copy.deepcopy(tpl[0])
                t.pop("polygon", None)  # (a polygon becomes that shape)
            self.shapes_changed()
        if self.tool.get() in BOX_TOOLS:  # Circle / Polygon: a library shape picked = back to Custom shape
            self.tool.set("custom")
        self.sync_custom()
        self.schedule_autosave()

    def set_custom(self, key, value):
        """A custom shape setting (fill / gate / align / the on-off CUSTOM_FLAGS) changed in the panel."""
        if self._loading:
            return
        tgts = self.custom_targets()
        placed = [t for t in tgts if t is not self.custom_defaults]
        same = all(abs(t[key] - value) < 1e-12 if key == "gate" else bool(t.get(key)) == value if key in CUSTOM_FLAGS
                   else t.get(key) == value for t in tgts)
        if same or not self.confirm_big([dict(t, **{key: value}) for t in placed]):
            return self.sync_custom()
        if placed:
            self.push_undo(name=CUSTOM_NAMES.get(key, key))
        for t in tgts:
            t[key] = value
            if key in CUSTOM_FLAGS and not value and t is not self.custom_defaults:
                del t[key]  # (shapes only have them when they're on)
        self.shapes_changed()
        self.sync_custom()
        if key == "fill" and value != "empty":
            self.tips.show("fill", wait=True)

    def on_gate(self):
        if self._loading or str(self.gate_entry.cget("state")) == "disabled":
            return
        try:
            ticks = calc(self.gate_var.get())
            if not 1 <= ticks <= 10 ** 7:
                raise ValueError
        except ValueError:
            self.gate_entry.config(style="Bad.TEntry")
            return
        self.set_custom("gate", ticks / self.ppq)

    # ---- Hz bass (custom.py): spam whose gate is one wave of a tone

    def current_bpm(self):
        """The project's BPM, or None while its box holds something else."""
        try:
            bpm = calc(self.pvar["bpm"].get())
        except ValueError:
            return None
        return bpm if 4 <= bpm <= 100000 else None

    def sync_hz(self, tgts, spam):
        """The Hz bass row shows tgts' settings. Returns True if Hz bass is on (and the fill is a spam one)."""
        hz = tgts[0].get("hz")
        self._loading = True
        self.hz_var.set(bool(hz))
        if hz or not self.hz_tone.get():
            self.hz_tone.set(note_name((hz or HZ_DEFAULTS)["key"]))
            self.hz_cents.set(fmt((hz or HZ_DEFAULTS)["cents"]))
        self._loading = False
        on = bool(hz) and spam
        self.hz_check.config(state="normal" if spam else "disabled")
        for entry in (self.hz_tone_entry, self.hz_cents_entry):
            entry.config(state="normal" if on else "disabled", style="TEntry")
        bpm = self.current_bpm()
        stale = on and bpm is not None and any(t.get("hz") and abs(t["hz"]["bpm"] - bpm) > 1e-9 for t in tgts)
        self.hz_info.pack_forget()
        self.hz_stale.pack_forget()
        if on:
            gate = max(1.0, tgts[0]["gate"] * self.ppq)
            low = math.floor(gate)
            text = (tr("panel_custom.hz_info_whole", hz=f"{hz_of(hz['key'], hz['cents']):.2f}", gate=low)
                    if gate - low < 1e-9 else
                    tr("panel_custom.hz_info", hz=f"{hz_of(hz['key'], hz['cents']):.2f}", gate=f"{gate:.3f}",
                       low=low, high=low + 1))
            short = gate < HZ_SHORT
            self.hz_info.config(text=text + (tr("panel_custom.hz_short") if short else ""),
                                foreground=GAP_COLOR if short else "#777")
            self.hz_info.pack(anchor="w", padx=(20, 0), after=self.hz_row)
            if stale:
                self.hz_stale.pack(anchor="w", padx=(20, 0), after=self.hz_info)
        return on

    def on_hz(self):
        """The Hz bass box ticked or cleared."""
        if self._loading:
            return
        self.set_hz((self.typed_hz() or dict(HZ_DEFAULTS)) if self.hz_var.get() else None)

    def typed_hz(self):
        """The tone and pitch boxes as {"key", "cents"}, or None (the wrong box turns red)."""
        out = {}
        for name, entry, read in (("key", self.hz_tone_entry, lambda: tone_key(self.hz_tone.get())),
                                  ("cents", self.hz_cents_entry, lambda: float(calc(self.hz_cents.get())))):
            try:
                out[name] = read()
                if name == "cents" and abs(out[name]) > 1200:
                    raise ValueError
            except ValueError:
                entry.config(style="Bad.TEntry")
                return None
        return out

    def on_hz_entry(self):
        if self._loading or str(self.hz_tone_entry.cget("state")) == "disabled":
            return
        hz = self.typed_hz()
        if hz:
            self.set_hz(hz)

    def set_hz(self, hz):
        """Hz bass on (hz = {"key", "cents"}: the gate is worked out for the BPM now) or off (None) for the custom
        shape panel's targets."""
        tgts, bpm = self.custom_targets(), self.current_bpm()
        if hz and bpm is None:
            return self.sync_custom()
        new = {"hz": dict(hz, bpm=float(bpm)), "gate": hz_gate(hz, bpm)} if hz else {}
        placed = [t for t in tgts if t is not self.custom_defaults]
        if (all(t.get("hz") == new.get("hz") for t in tgts)
                or not self.confirm_big([dict({k: v for k, v in t.items() if k != "hz"}, **new) for t in placed])):
            return self.sync_custom()
        if placed:
            self.push_undo(name=tr("panel_custom.hz_bass"))
        for t in tgts:
            t.pop("hz", None)
            t.update(copy.deepcopy(new))
        self.shapes_changed()
        self.sync_custom()
        self.schedule_autosave()

    def update_hz(self):
        """The "Update Hz bass" button: every target's gate worked out again for the BPM now."""
        bpm = self.current_bpm()
        tgts = [t for t in self.custom_targets() if t.get("hz")]
        if bpm is None or not tgts:
            return
        if any(t is not self.custom_defaults for t in tgts):
            self.push_undo(name=tr("panel_custom.hz_update"))
        for t in tgts:
            t["hz"] = dict(t["hz"], bpm=float(bpm))
            t["gate"] = hz_gate(t["hz"], bpm)
        self.shapes_changed()
        self.sync_custom()
        self.schedule_autosave()

    def open_drawer(self):
        if self.drawer:
            self.drawer.deiconify()
            self.drawer.lift()
            return
        self.drawer = Drawer(self)
        name = self.custom_pick.get() or self.custom_shape
        strokes = load_shape(name) if name else None
        if strokes:
            self.drawer.open_shape(name, strokes)

    def save_to_library(self, sh):
        """A custom shape's drawing (e.g. drawn live) into the shape library, under a name asked for. It keeps the
        proportions it has on screen now."""
        live = sh["name"] == tr("custom.live_drawing")
        name = clean_name(simpledialog.askstring(tr("panel_custom.spiderweb"),
                                                 tr("panel_custom.save_the_drawing_to_the_shape"),
                                                 initialvalue="" if live else sh["name"], parent=self) or "")
        if not name:
            return
        if name.lower() in (n.lower() for n in library_names()) and not messagebox.askyesno(
                tr("panel_custom.spiderweb"), tr("panel_custom.is_already_in_the_library_replace",
                                                 name=name), parent=self):
            return
        (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
        w = math.hypot((b1 - b0) * self.roll.sx, (p1 - p0) * self.roll.sy)  # the box on screen
        h = math.hypot((b2 - b0) * self.roll.sx, (p2 - p0) * self.roll.sy)
        if w > 1e-9 and h > 1e-9:
            su, sv = (1.0, h / w) if w >= h else (w / h, 1.0)
            strokes = [map_stroke(st, lambda u, v: (u * su, v * sv), su, sv) for st in sh["strokes"]]
        else:
            strokes = copy.deepcopy(sh["strokes"])
        try:
            save_shape(name, join_strokes(strokes))
        except OSError as e:
            messagebox.showerror(tr("panel_custom.spiderweb"), tr("panel_custom.couldn_t_save", e=e), parent=self)
            return
        sh["name"] = name
        self.refresh_custom_names()
        if self.drawer:
            self.drawer.refresh_list()
        self.shapes_changed()
        self.sync_custom()
        self.status.config(text=tr("panel_custom.saved_to_the_shape_library", name=name))

    def use_custom(self, name):
        """The drawer's "Use on the piano roll": new custom shapes are made of this one."""
        self.custom_shape = name
        self.refresh_custom_names()
        self.select(None)
        self.tool.set("custom")
        self.sync_custom()
        self.lift()
        self.roll.focus_set()
        self.schedule_autosave()
