"""The side panel's custom shape settings (which shape, how it's filled) and the drawer window."""

import copy
import math
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from files.lang import tr
from notes.custom import (CUSTOM_FLAGS, SPAM_FILLS, box_frame, custom_settings, gap_lines, join_strokes, map_stroke,
                          normalize_strokes, open_paths)
from window.drawer import Drawer, clean_name, library_names, load_shape, save_shape
from window.panel_funnel import GATE_STEPS
from files.mathexpr import calc, fmt
from roll.roll_live import BOX_TOOLS, STROKE_TOOLS
from window.widgets import Scrub, Tooltip

GAP_COLOR = "#c06000"  # Fill / Spam on a shape whose outline has one gap (closed with a straight line)
FILL_CHOICES = [
    ("empty", tr("panel_custom.empty"), tr("panel_custom.just_the_outline_like_lines")),
    ("fill", tr("panel_custom.fill"), tr("panel_custom.one_long_note_per_key_inside")),
    ("spam", tr("panel_custom.spam"), tr("panel_custom.the_inside_filled_with_back_to")),
    ("outline_spam", tr("panel_custom.outline_spam"), tr("panel_custom.just_the_outline_chopped_into_notes")),
]
CUSTOM_NAMES = {"fill": tr("panel_custom.inside_fill"), "gate": tr("panel_custom.spam_gate"),
                "align": tr("panel_custom.spam_start"), "union": tr("panel_custom.overlaps_cancel_out"),
                "apart": tr("panel_custom.normal_outline")}  # (History)
APART_CHOICES = [tr("panel_custom.normal"), tr("panel_custom.outline")]
APART_TIP = tr("panel_custom.normal_all_its_notes_together_outline")
APART_NEEDS = tr("panel_custom.channels_isn_t_multi_channel_now")
CANCEL_TIP = tr("panel_custom.fill_and_spam_where_outlines_overlap")
ALIGN_CHOICES = [
    ("auto", tr("panel_custom.auto"), tr("panel_custom.each_key_s_notes_start_at")),
    ("aligned", tr("panel_custom.aligned"), tr("panel_custom.every_note_sits_on_the_gate")),
]


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
        for value, text, tip in FILL_CHOICES:
            line = ttk.Frame(opts)
            line.pack(anchor="w", fill="x")
            b = ttk.Radiobutton(line, text=text, value=value, variable=self.fill_var,
                                command=lambda: self.set_custom("fill", self.fill_var.get()))
            b.pack(side="left")
            self.fill_buttons[value] = b
            self.fill_tips[value] = Tooltip(b, tip)
            if value in ("fill", "spam"):
                drop = ttk.Combobox(line, textvariable=self.apart_var, values=APART_CHOICES, state="readonly",
                                    width=8)
                drop.pack(side="left", padx=(6, 0))
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
        # the tools that make custom shapes: Custom shape, Square, Circle, and with Live shape on the drawing tools
        live = self.live.get() and tool in STROKE_TOOLS
        makes_custom = tool in ("custom", "text") or tool in BOX_TOOLS or live
        # "last note" means nothing for custom shapes and funnels
        kinds = {self.shapes[i]["kind"] for i in self.sels} or {"custom" if makes_custom else tool}
        self._rows["last"] = not kinds <= {"custom", "funnel"}
        self._rows["custom"] = placed or bool(tgts and makes_custom)
        self.layout_rows()
        if not self._rows["custom"]:
            return
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
        self.gate_entry.config(state="normal" if spam else "disabled")
        for b in self.align_buttons:
            b.config(state="normal" if spam else "disabled")
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
            self.shapes_changed()
        if self.tool.get() in BOX_TOOLS:  # Square / Circle / Triangle: a library shape picked = back to Custom shape
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
