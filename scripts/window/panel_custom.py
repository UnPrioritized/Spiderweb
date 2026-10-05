"""The side panel's custom shape settings (which shape, how it's filled) and the drawer window."""

import copy
import math
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from files.lang import tr
from notes.areas import COLOURS
from notes.custom import (CUSTOM_FLAGS, ENDS, HZ_DEFAULTS, SPAM_FILLS, box_frame, custom_settings, edge_gate, gap_lines,
                          gate_ticks, hz_gate, join_strokes, map_stroke, normalize_areas, normalize_strokes, open_paths)
from window.drawer import Drawer, clean_name, library_names, load_drawing, save_shape, shape_stamp
from window.panel_funnel import GATE_STEPS
from files.mathexpr import calc, fmt
from roll.roll_live import BOX_TOOLS, STROKE_TOOLS
from notes.hzbass import AUTO, auto_picks, shortest_gate
from window.hz_window import open_hz
from window.range_window import open_range_graph
from window.widgets import Scrub, Tooltip, bad, good, grid_shown

GAP_COLOR = "#c06000"  # Fill / Spam on a shape whose outline has one gap (closed with a straight line)
MISSING_MARK = "✕ "  # in the Shape box: a placed shape whose library shape was renamed or deleted (user, 2026-10-05)
MISSING_COLOR = "#808080"
FILL_CHOICES = [
    ("empty", tr("panel_custom.empty"), tr("panel_custom.just_the_outline_like_lines")),
    ("fill", tr("panel_custom.fill"), tr("panel_custom.one_long_note_per_key_inside")),
    ("spam", tr("panel_custom.spam"), tr("panel_custom.the_inside_filled_with_back_to")),
    ("outline_spam", tr("panel_custom.outline_spam"), tr("panel_custom.just_the_outline_chopped_into_notes")),
]
CUSTOM_NAMES = {"fill": tr("panel_custom.inside_fill"), "gate": tr("panel_custom.spam_gate"),
                "align": tr("panel_custom.spam_start"), "ends": tr("panel_custom.spam_ends"),
                "union": tr("panel_custom.overlaps_cancel_out"),
                "apart": tr("panel_custom.normal_outline"), "edge": tr("panel_custom.edge_undo"),
                "borders": tr("panel_custom.borders"),
                "edge_mode": tr("panel_custom.edge_undo")}  # (History)
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
EDGE_CHOICES = [(None, tr("panel_custom.edge_band")), ("sideways", tr("panel_custom.edge_sideways"))]
HZ_SHORT = 40  # Hz bass: a gate under this many ticks wobbles between its two sizes (orange: a higher PPQ fixes it)
HZ_WRAP = 222  # its lines under the Hz bass row start further right than the panel's other notes: wrapped sooner


def hz_tool(sh):
    """A Hz bass made with the Hz bass tool (hz["own"]): a musical tool, not a shape (user): no library shape to
    pick and no "Overlaps cancel out" for it."""
    return bool((sh.get("hz") or {}).get("own"))


class CustomPanel:
    """Mixed into App."""

    def _build_custom(self):
        """Custom shape settings: which library shape, and how the inside is filled."""
        self._templates = {}  # custom_template's library shapes read: {name in lower case: (shape_stamp, template)}
        box = self.custom_box = ttk.Frame(self.settings)
        row = self.custom_shape_row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text=tr("panel_custom.shape")).pack(side="left")
        self.custom_combo = ttk.Combobox(row, textvariable=self.custom_pick, state="readonly", width=18,
                                         postcommand=self.refresh_custom_names)
        self.custom_combo.pack(side="left", padx=(5, 0))
        self.custom_combo.bind("<<ComboboxSelected>>", lambda e: self.on_custom_pick())
        ttk.Button(row, text=tr("panel_custom.drawer"), command=self.open_drawer).pack(side="left", padx=(4, 0))
        # under the Shape box while a placed shape's library shape was renamed or deleted (not_in_library)
        self.missing_note = ttk.Label(box, text=tr("panel_custom.not_in_library"), foreground=GAP_COLOR,
                                      font=("Segoe UI", 8), wraplength=int(300 * self.scale), justify="left")
        row = self.custom_fill_row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text=tr("panel_custom.inside")).pack(side="left", anchor="n")
        opts = ttk.Frame(row)
        opts.pack(side="left", padx=(5, 0))
        self.fill_buttons = {}
        self.fill_tips = {}  # (Fill / Spam also say why they're orange (one gap) or greyed out (more))
        ttk.Style(self).configure("Gap.TRadiobutton", foreground=GAP_COLOR)
        ttk.Style(self).configure("Gap.TCombobox", foreground=GAP_COLOR)
        ttk.Style(self).configure("Missing.TCombobox", foreground=MISSING_COLOR)
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
        # the rows under the choices: only the ones that do something for the chosen Inside are shown, so the
        # panel grows as options are picked (user; show_custom_rows)
        self.custom_rows, self._custom_shown = [], None
        # with "Outline" and areas coloured in the drawer: where two colours meet is outline too
        self.borders_var = tk.BooleanVar(value=False)
        self.borders_box = ttk.Checkbutton(opts, text=tr("panel_custom.borders"), variable=self.borders_var,
                                           command=lambda: self.set_custom("borders", self.borders_var.get()))
        self.custom_rows.append((self.borders_box, dict(anchor="w", padx=(20, 0), pady=(1, 0))))
        Tooltip(self.borders_box, tr("panel_custom.borders_tip"))
        # more colours than a shape can have (outline and own fill count): the extra ones merged into the last
        self.colours_warn = ttk.Label(opts, text="", foreground=GAP_COLOR, font=("Segoe UI", 8),
                                      wraplength=int(HZ_WRAP * self.scale), justify="left")
        self.custom_rows.append((self.colours_warn, dict(anchor="w", padx=(20, 0))))
        g = self.gate_row = ttk.Frame(opts)
        self.custom_rows.append((g, dict(anchor="w", padx=(20, 0))))
        lb = ttk.Label(g, text=tr("panel_custom.gate"))
        lb.pack(side="left")
        self.gate_entry = ttk.Entry(g, textvariable=self.gate_var, width=7)
        self.gate_entry.pack(side="left", padx=4)
        ttk.Label(g, text=tr("panel_custom.ticks_enter_to_apply"), foreground="#777").pack(side="left")
        self.gate_entry.bind("<Return>", lambda e: self.on_gate())
        self.gate_entry.bind("<FocusOut>", lambda e: self.on_gate(left=True))
        self._shown = {}  # what sync_custom put in the Gate / Outline gate boxes (leaving one unchanged does nothing)
        self._hz_ok = None  # the shapes skip_hz was OK'd for (not asked again while they stay selected)
        Scrub(self, [(self.gate_entry, self.gate_var, self.on_gate)], GATE_STEPS, 1, 10 ** 7, label=lb)
        # with a Range on, the gate box is orange: it's the Range's first gate, and a new number here takes it off
        ttk.Style(self).configure("Gap.TEntry", foreground=GAP_COLOR)
        self.gate_tip = Tooltip(self.gate_entry, "")
        # Range (gaterange.py): the gate goes from this one to a second one across the shape, along a graph. All
        # of it is set in its own window (user: room for more options there, not in the panel)
        self.range_btn = ttk.Button(g, text=tr("panel_custom.range"), command=lambda: open_range_graph(self))
        self.range_btn.pack(side="left", padx=(8, 0))
        Tooltip(self.range_btn, tr("panel_custom.range_tip"))
        # Hz bass: the gate is one wave of a tone (custom.py). Only the switch, Notes… and the warnings here (user);
        # the rest is in the Hz bass window
        h = self.hz_row = ttk.Frame(opts)
        self.custom_rows.append((h, dict(anchor="w", padx=(20, 0), pady=(1, 0))))
        self.hz_var = tk.BooleanVar()
        self.hz_check = ttk.Checkbutton(h, text=tr("panel_custom.hz_bass"), variable=self.hz_var, command=self.on_hz)
        self.hz_check.pack(side="left")
        Tooltip(self.hz_check, tr("panel_custom.hz_tip"))
        self.hz_notes_btn = ttk.Button(h, text=tr("panel_custom.hz_notes"), command=lambda: open_hz(self))
        self.hz_notes_btn.pack(side="left", padx=(6, 0))
        Tooltip(self.hz_notes_btn, tr("panel_custom.hz_notes_tip"))
        self.hz_info = ttk.Label(opts, text="", foreground=GAP_COLOR, font=("Segoe UI", 8),  # (short gates)
                                 wraplength=int(HZ_WRAP * self.scale), justify="left")
        self.hz_stale = ttk.Frame(opts)  # the BPM changed since: its tone is off until it's updated
        ttk.Label(self.hz_stale, text=tr("panel_custom.hz_stale"), foreground=GAP_COLOR, font=("Segoe UI", 8),
                  wraplength=int(HZ_WRAP * self.scale), justify="left").pack(anchor="w")
        ttk.Button(self.hz_stale, text=tr("panel_custom.hz_update"), command=self.update_hz).pack(anchor="w", pady=(1, 2))
        for w in (self.hz_info, self.hz_stale):
            self.custom_rows.append((w, dict(anchor="w", padx=(20, 0))))
        e = self.ends_row = ttk.Frame(opts)
        self.custom_rows.append((e, dict(anchor="w", padx=(20, 0), pady=(1, 0))))
        ttk.Label(e, text=tr("panel_custom.ends")).pack(side="left")
        self.ends_var = tk.StringVar(value=END_CHOICES[0][1])
        self.ends_box = ttk.Combobox(e, textvariable=self.ends_var, values=[t for _, t in END_CHOICES],
                                     state="readonly", width=15)
        self.ends_box.pack(side="left", padx=4)
        self.ends_box.bind("<<ComboboxSelected>>", lambda ev: (
            self.set_custom("ends", ENDS[self.ends_box.current()]), self.roll.focus_set()))
        Tooltip(self.ends_box, tr("panel_custom.ends_tip"))
        a = self.start_row = ttk.Frame(opts)
        self.custom_rows.append((a, dict(anchor="w", padx=(20, 0), pady=(1, 0))))
        ttk.Label(a, text=tr("panel_custom.start")).pack(side="left")
        self.align_buttons = []
        for value, text, tip in ALIGN_CHOICES:
            b = ttk.Radiobutton(a, text=text, value=value, variable=self.align_var,
                                command=lambda: self.set_custom("align", self.align_var.get()))
            b.pack(side="left", padx=(4, 0))
            Tooltip(b, tip)
            self.align_buttons.append(b)
        # the smallest outline gate (custom.grow_inward): thin outline notes grow into the inside
        o = self.edge_row = ttk.Frame(opts)
        self.custom_rows.append((o, dict(anchor="w", padx=(20, 0), pady=(1, 0))))
        lb = ttk.Label(o, text=tr("panel_custom.edge"))
        lb.pack(side="left")
        self.edge_var = tk.StringVar()
        self.edge_entry = ttk.Entry(o, textvariable=self.edge_var, width=7)
        self.edge_entry.pack(side="left", padx=4)
        ttk.Label(o, text=tr("panel_custom.edge_unit"), foreground="#777").pack(side="left")
        for w in (lb, self.edge_entry):
            Tooltip(w, tr("panel_custom.edge_tip"))
        o = self.edge_mode_row = ttk.Frame(opts)
        self.custom_rows.append((o, dict(anchor="w", padx=(40, 0), pady=(1, 0))))
        self.edge_mode_box = ttk.Combobox(o, values=[t for _, t in EDGE_CHOICES], state="readonly", width=15)
        self.edge_mode_box.pack(side="left")
        self.edge_mode_box.bind("<<ComboboxSelected>>", lambda e: (self.set_custom(
            "edge_mode", EDGE_CHOICES[self.edge_mode_box.current()][0]), self.roll.focus_set()))
        Tooltip(self.edge_mode_box, tr("panel_custom.edge_mode_tip"))
        self.edge_entry.bind("<Return>", lambda e: self.on_edge())
        self.edge_entry.bind("<FocusOut>", lambda e: self.on_edge(left=True))
        Scrub(self, [(self.edge_entry, self.edge_var, self.on_edge)], GATE_STEPS, 0, 10 ** 7, label=lb)
        # while the box is pointed at, has the keyboard or its label is dragged: a faint line on the piano roll
        # where the outline would reach inside (roll_draw.draw_edge_preview; user)
        self._edge_use = set()
        for w, on, off in ((lb, "<Enter>", "<Leave>"), (self.edge_entry, "<Enter>", "<Leave>"),
                           (self.edge_entry, "<FocusIn>", "<FocusOut>"), (lb, "<ButtonPress-1>", "<ButtonRelease-1>")):
            why = (str(w), on)
            w.bind(on, lambda e, why=why: self.edge_using(why, True), add="+")
            w.bind(off, lambda e, why=why: self.edge_using(why, False), add="+")
        self.edge_var.trace_add("write", lambda *_: self.edge_using(None, None))
        self.cancel_var = tk.BooleanVar(value=True)
        self.cancel_box = ttk.Checkbutton(opts, text=tr("panel_custom.overlaps_cancel_out"), variable=self.cancel_var,
                                          command=lambda: self.set_custom("union", not self.cancel_var.get()))
        self.custom_rows.append((self.cancel_box, dict(anchor="w", padx=(20, 0), pady=(2, 0))))
        Tooltip(self.cancel_box, CANCEL_TIP)
        self.custom_info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                                     wraplength=int(300 * self.scale), justify="left")
        self.custom_info.pack(fill="x", pady=(2, 0))

    def refresh_custom_names(self):
        self.custom_combo.config(values=library_names())

    def custom_template(self, name):
        """A library shape ready to place: (strokes filling the 0..1 box, width/height as drawn, its areas coloured
        by hand moved the same way), or None."""
        if not name:
            return None
        now = shape_stamp(name)  # (kept until its file changes: a big shape took 0.7 s to read, on every click)
        kept = self._templates.get(name.lower())
        if kept and kept[0] == now:
            return kept[1]
        strokes, areas = load_drawing(name)
        tpl = normalize_strokes(strokes) + (normalize_areas(strokes, areas),) if strokes else None
        if len(self._templates) >= 8:
            self._templates.clear()
        self._templates[name.lower()] = (now, tpl)
        return tpl

    def not_in_library(self, sh):
        """A placed shape picked from the library whose library shape has been renamed or deleted since (it keeps
        its own drawing, the name is only where it came from). Shapes drawn on the roll go by the tool's name."""
        name = sh.get("name", "")
        made = {t.title() for t in STROKE_TOOLS} | {tr("custom.live_drawing"), tr("hz.name")}
        if sh.get("polygon") or sh.get("text") or "notes" in sh or name in made:
            return False
        return name.lower() not in {n.lower() for n in library_names()}

    def new_custom(self, strokes, b0, p0, b1, p1, areas=(), drawn=None):
        """A library shape placed in a box. drawn: its width / height as drawn (custom_template), the proportions it
        keeps (custom.drawn_view: in its 0..1 box one v was 1 / drawn u)."""
        sh = dict(self.defaults, kind="custom", name=self.custom_shape, strokes=copy.deepcopy(strokes),
                  **custom_settings(self.custom_defaults), pts=box_frame(b0, p0, b1, p1))
        if drawn:
            sh["round"] = 1 / drawn
        if areas:
            sh["areas"] = copy.deepcopy(list(areas))
        return sh

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
        makes_custom = tool in ("custom", "text", "hz") or tool in BOX_TOOLS or live
        # "last note" means nothing for custom shapes and funnels
        kinds = {self.shapes[i]["kind"] for i in self.sels} or {"custom" if makes_custom else tool}
        self._rows["last"] = not kinds <= {"custom", "funnel"}
        self._rows["custom"] = placed or bool(tgts and makes_custom)
        self.layout_rows()
        if not self._rows["custom"]:
            return
        self.sync_polygon()
        # text: no library shape to pick (its letters are the shape; a Hz bass made with the Hz bass tool is a
        # musical tool, not a shape, user: its box is only its notes)
        text = all(t.get("text") or "notes" in t or hz_tool(t) for t in tgts) if placed else tool in ("text", "hz")
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
            self.missing_note.pack_forget()
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
        missing = placed and self.not_in_library(tgts[0])
        self.custom_combo.config(style="Missing.TCombobox" if missing else "TCombobox")
        if missing and not self.missing_note.winfo_manager():
            self.missing_note.pack(fill="x", pady=(2, 0), after=self.custom_shape_row)
        elif not missing and self.missing_note.winfo_manager():
            self.missing_note.pack_forget()
        self._loading = True
        self.custom_pick.set(MISSING_MARK + name if missing else name)
        self.fill_var.set(fill)
        self.gate_var.set(fmt(gate_ticks(gate, self.ppq)))  # (the whole ticks the notes use, never a fraction)
        self.edge_var.set(fmt(edge_gate(tgts[0], self.ppq)))
        self._shown = {"gate": self.gate_var.get(), "edge": self.edge_var.get()}
        self.edge_entry.config(style="TEntry")
        self.align_var.set(tgts[0].get("align", "auto"))
        ends = tgts[0].get("ends", "drop")
        self.ends_box.current(ENDS.index(ends) if ends in ENDS else 0)
        rg = tgts[0].get("range")
        self.gate_entry.config(style="Gap.TEntry" if rg else "TEntry")
        self.gate_tip.text = tr("panel_custom.gate_ranged_tip", a=gate_ticks(gate, self.ppq),
                                b=gate_ticks(rg["to"], self.ppq)) if rg else ""
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
        hz = self.sync_hz(tgts, spam, placed)  # (Hz bass: its own gate and grid, so gate / ends / start are off)
        self.gate_entry.config(state="normal" if spam and not hz else "disabled")
        self.ends_box.config(state="readonly" if spam and not hz else "disabled")
        self.range_btn.config(state="normal" if spam and not hz else "disabled")
        for value in self.fill_buttons:  # (Hz bass needs a spam fill: Empty / Fill hidden until it's unticked, user)
            if value not in SPAM_FILLS:
                grid_shown(self.fill_buttons[value], not hz)
        ranged = spam and not hz and bool(rg)  # (Range: its own grid from the shape's left edge, so no ends / start)
        for b in self.align_buttons:  # (stretched gates fill each key exactly: where they start doesn't matter)
            b.config(state="normal" if spam and not hz and ends != "stretch" else "disabled")
        self._loading = True
        self.cancel_var.set(not tgts[0].get("union"))
        apart = bool(tgts[0].get("apart"))
        self.apart_var.set(APART_CHOICES[apart])
        self.borders_var.set(bool(tgts[0].get("borders")))
        self._loading = False
        lonely = apart and self.channel_mode.get() != "auto"  # (Outline without Multi channel: all one channel)
        for value, box in self.apart_boxes.items():
            box.config(state="readonly" if fill == value else "disabled",
                       style="Gap.TCombobox" if lonely and fill == value else "TCombobox")
            grid_shown(box, fill == value)  # (only next to the chosen one)
            self.apart_tips[value].text = APART_TIP + (APART_NEEDS if lonely else "")
        outline = fill in ("empty", "outline_spam") or apart  # (Fill / Spam: only with "Outline")
        self.edge_entry.config(state="normal" if outline else "disabled")
        self._loading = True
        self.edge_mode_box.current(1 if tgts[0].get("edge_mode") == "sideways" else 0)
        self._loading = False
        self.edge_mode_box.config(state="readonly" if outline and tgts[0].get("edge") else "disabled")
        self.cancel_box.config(state="normal" if fill in ("fill", "spam") and not text else "disabled")
        wanted = max([self.colours_wanted[i] for i, s in enumerate(self.shapes) if any(s is t for t in tgts)
                      and i < len(self.colours_wanted)] or [0])
        if wanted > COLOURS:
            self.colours_warn.config(text=tr("panel_custom.too_many_colours", n=wanted, most=COLOURS))
        rows = {self.colours_warn: wanted > COLOURS, self.gate_row: spam and not hz, self.hz_row: spam, self.hz_info: self.hz_short_on,
                self.hz_stale: self.hz_stale_on, self.ends_row: spam and not hz and not ranged,
                self.start_row: spam and not hz and ends != "stretch" and not ranged,
                self.edge_row: outline,
                self.edge_mode_row: outline and bool(tgts[0].get("edge")),
                self.cancel_box: fill in ("fill", "spam") and not text,
                self.borders_box: fill in ("fill", "spam") and apart and self.coloured_areas(tgts, placed, name)}
        self.show_custom_rows({w for w, on in rows.items() if on})
        if not placed and live:
            info = tr("panel_custom.live_shape_what_you_draw_goes")
        elif not placed and tool == "text":
            info = tr("panel_custom.inside_fill_for_new_text")
        elif not placed and tool == "hz":
            info = tr("panel_custom.hz_tool_info")
        elif not placed and tool in BOX_TOOLS:
            info = tr("panel_custom.drag_a_box_on_the_piano", tool=tool)
        elif not placed and not self.custom_template(name):
            info = tr("panel_custom.pick_a_shape_or_make_one")
        elif placed:
            info = tr("panel_custom.n_notes", n=sum(self.note_count(t) for t in tgts))
            if gaps and fill in ("fill", "spam"):
                info += (tr("panel_custom.one_gap_in_the_outline_filled") if gaps == 1 else
                         tr("panel_custom.gaps_in_the_outline_filled_as", gaps=gaps))
            if any(t.get("areas") for t in tgts):  # (areas coloured in the drawer: areas.py)
                if fill not in ("fill", "spam"):
                    info += tr("panel_custom.areas_need_fill")
                elif self.channel_mode.get() != "auto" and any(a[2] for t in tgts for a in t.get("areas", ())):
                    info += tr("panel_custom.areas_need_multi")
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
                if t is not self.custom_defaults and not t.get("text") and "notes" not in t and not hz_tool(t)]
        if tgts:
            self.push_undo(name=tr("panel_custom.custom_shape"))
            for t in tgts:
                t["name"], t["strokes"] = name, copy.deepcopy(tpl[0])
                t.pop("areas", None)
                if tpl[2]:
                    t["areas"] = copy.deepcopy(tpl[2])
                t.pop("round", None)  # (the proportions it was drawn in come along, like new_custom: outline gate)
                if tpl[1]:
                    t["round"] = 1 / tpl[1]
                t.pop("polygon", None)  # (a polygon becomes that shape)
            self.shapes_changed()
        if self.tool.get() in BOX_TOOLS:  # Circle / Polygon: a library shape picked = back to Custom shape
            self.tool.set("custom")
        self.sync_custom()
        self.schedule_autosave()

    def coloured_areas(self, tgts, placed, name):
        """Some area of these shapes (or of the library shape new ones are made of) has a colour (areas.py)."""
        if placed:
            return any(a[2] for t in tgts for a in t.get("areas", ()))
        tpl = self.custom_template(name) if self.tool.get() == "custom" else None
        return bool(tpl and any(a[2] for a in tpl[2]))

    def set_custom(self, key, value):
        """A custom shape setting (fill / gate / align / the on-off CUSTOM_FLAGS) changed in the panel."""
        if self._loading:
            return
        tgts = self.custom_targets()

        def same(ts):
            return all(abs(t[key] - value) < 1e-12 if key == "gate" else abs(t.get(key, 0) - value) < 1e-12
                       if key == "edge" else bool(t.get(key)) == value if key in CUSTOM_FLAGS
                       else t.get(key) == value for t in ts)

        if same(tgts):  # (nothing to change: no Hz bass warning either)
            return self.sync_custom()
        if key == "gate" or key == "fill" and value not in SPAM_FILLS:  # (a Hz bass's gate is its tone's)
            tgts = self.skip_hz(tgts)
            if not tgts or same(tgts):
                return self.sync_custom()
        placed = [t for t in tgts if t is not self.custom_defaults]
        if not self.confirm_big([dict(t, **{key: value}) for t in placed]):
            return self.sync_custom()
        if placed:
            self.push_undo(name=CUSTOM_NAMES.get(key, key))
        for t in tgts:
            t[key] = value
            if key == "gate" and t.get("range"):  # (a new gate typed in the panel = one flat gate again, user;
                t["range_kept"] = t.pop("range")  # kept for the Range window to bring back)
            if not value and (key in ("edge", "edge_mode") or key in CUSTOM_FLAGS and t is not self.custom_defaults):
                t.pop(key, None)  # (shapes only have them when they're on)
        self.shapes_changed()
        self.sync_custom()
        if key == "fill" and value != "empty":
            self.tips.show("fill", wait=True)

    def skip_hz(self, tgts):
        """Spam shapes and a Hz bass picked together: a gate / Range / Fill or Empty change leaves the Hz bass as it
        is (user: they don't mix), after an OK / Cancel warning. [] = Cancel (or only Hz bass). Asked once (user: it
        came at every step of a gate drag): OK holds while the same shapes stay selected, Cancel for the rest of
        that drag / arrow-key stepping."""
        rest = [t for t in tgts if not t.get("hz")]
        if len(rest) == len(tgts) or not rest:
            return rest
        picked = {id(t) for t in tgts}
        if self._hz_ok == picked:
            return rest
        sc = self._scrub
        if sc and sc.get("hz_no"):
            return []
        ok = messagebox.askokcancel(tr("panel_custom.spiderweb"), tr("panel_custom.hz_skipped"), icon="warning",
                                    parent=self)
        self._hz_ok = picked if ok else None
        if sc and not ok:
            sc["hz_no"] = True
        return rest if ok else []

    def on_gate(self, left=False):
        """The Gate box entered (whole ticks: a fraction is rounded, user). left: the box was only left, so nothing
        happens unless its number was changed (several shapes with different gates all got the first one's)."""
        if (self._loading or str(self.gate_entry.cget("state")) == "disabled"
                or left and self.gate_var.get() == self._shown.get("gate")):
            return
        try:
            ticks = calc(self.gate_var.get())
            if not 0.5 <= ticks <= 10 ** 7:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(self.gate_entry)
            return
        self.set_custom("gate", math.floor(ticks + 0.5) / self.ppq)
        good(self.gate_entry)  # (after: the box shows the whole ticks now)

    def commit_typing(self):
        """A number typed in the Gate / Outline gate box but not entered goes to the shapes it was typed for, before
        the selection changes (user: a click on the piano roll lost it, or gave it to new shapes)."""
        try:
            w = self.focus_get()
        except KeyError:  # (a dropdown's list has the keyboard)
            return
        if w is self.gate_entry:
            self.on_gate(left=True)
        elif w is self.edge_entry:
            self.on_edge(left=True)

    def edge_using(self, why, on):
        """The outline gate box started / stopped being used (why: which way; None = its number changed): the
        preview line on the piano roll follows what's in the box while it's valid."""
        if why is not None:
            (self._edge_use.add if on else self._edge_use.discard)(why)
        g = None
        if self._edge_use and str(self.edge_entry.cget("state")) != "disabled":
            try:
                ticks = calc(self.edge_var.get())
                if 0.5 <= ticks <= 10 ** 7:
                    g = math.floor(ticks + 0.5) / self.ppq
            except (ValueError, ZeroDivisionError):
                pass
        if g != getattr(self, "edge_preview", None):
            self.edge_preview = g
            self.roll.request_redraw()

    def on_edge(self, left=False):
        """The smallest outline gate box (whole ticks; 0 = off). left: like on_gate's."""
        if (self._loading or str(self.edge_entry.cget("state")) == "disabled"
                or left and self.edge_var.get() == self._shown.get("edge")):
            return
        try:
            ticks = calc(self.edge_var.get() or "0")
            if not 0 <= ticks <= 10 ** 7:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(self.edge_entry)
            return
        self.set_custom("edge", math.floor(ticks + 0.5) / self.ppq)
        good(self.edge_entry)

    # ---- Hz bass (custom.py): spam whose gate is one wave of a tone

    def current_bpm(self):
        """The project's BPM, or None while its box holds something else."""
        try:
            bpm = calc(self.pvar["bpm"].get())
        except ValueError:
            return None
        return bpm if 4 <= bpm <= 100000 else None

    def sync_hz(self, tgts, spam, placed):
        """The Hz bass row shows tgts' settings, and the warnings (short gates, BPM changed). Returns True if Hz
        bass is on (and the fill is a spam one)."""
        # (new shapes never start with Hz bass, custom_settings: for them the box is off, except for the Hz bass
        # tool, whose new notes use these settings)
        new_off = not placed and self.tool.get() != "hz"
        hz = None if new_off else tgts[0].get("hz")
        self._loading = True
        self.hz_var.set(bool(hz))
        self._loading = False
        on = bool(hz) and spam
        self.hz_check.config(state="normal" if spam and not new_off else "disabled")
        bpm = self.current_bpm()
        self.hz_stale_on = on and bpm is not None and any(t.get("hz") and abs(t["hz"]["bpm"] - bpm) > 1e-9
                                                          for t in tgts)
        self.hz_short_on = on and shortest_gate(dict(hz, bpm=bpm or hz["bpm"]), self.ppq) < HZ_SHORT
        if self.hz_short_on:
            text = tr("panel_custom.hz_short_fixed" if hz.get("fixed") else "panel_custom.hz_short").strip()
            self.hz_info.config(text=text)
        return on

    def show_custom_rows(self, want):
        """Shows only the rows under the Inside choices that are in want (in their own order)."""
        shown = [w for w, _ in self.custom_rows if w in want]
        if shown == self._custom_shown:
            return
        self._custom_shown = shown
        for w, _ in self.custom_rows:
            w.pack_forget()
        for w, opts in self.custom_rows:
            if w in want:
                w.pack(**opts)

    def on_hz(self):
        """The Hz bass box ticked or cleared."""
        if self._loading:
            return
        self.set_hz({k: HZ_DEFAULTS[k] for k in ("key", "cents")} if self.hz_var.get() else None)

    def set_hz_cents(self, cents):
        """The Hz bass window's Pitch box: every target's tone moved by its own cents (one undo step)."""
        tgts = [t for t in self.custom_targets() if t.get("hz") and t["hz"]["cents"] != cents]
        if self._loading or not tgts:
            return
        placed = [t for t in tgts if t is not self.custom_defaults]
        new = [dict(t["hz"], cents=float(cents)) for t in tgts]
        if not self.confirm_big([dict(t, hz=h, gate=hz_gate(h, h["bpm"])) for t, h in zip(tgts, new)
                                 if t is not self.custom_defaults]):
            return
        if placed:
            self.push_undo(name=tr("panel_custom.hz_bass"))
        for t, h in zip(tgts, new):
            t["hz"], t["gate"] = h, hz_gate(h, h["bpm"])
        self.shapes_changed()
        self.sync_custom()
        if self.hz_window:
            self.hz_window.sync()
        self.schedule_autosave()

    def set_hz(self, hz):
        """Hz bass on (hz = {"key", "cents"}: the gate is worked out for the BPM now) or off (None) for the custom
        shape panel's targets. A shape's placed tones, fixed gates and grow stay as they are."""
        tgts, bpm = self.custom_targets(), self.current_bpm()
        if hz and bpm is None:
            return self.sync_custom()

        def changed(t):
            if not hz:  # (off: the spam gate and Range it had before come back, user)
                rest = {k: v for k, v in t.items() if k not in ("hz", "before_hz")}
                was = t.get("before_hz") if t.get("hz") else None
                if was:
                    rest["gate"] = was["gate"]
                    if was.get("range") and rest.get("range_kept"):
                        rest["range"] = rest.pop("range_kept")
                return rest
            rest = {k: v for k, v in t.items() if k != "hz"}
            # (a new one: Auto gates, user)
            new = dict(rest, hz=dict(t.get("hz") or {"auto": AUTO}, bpm=float(bpm), **hz), gate=hz_gate(hz, bpm))
            if not t.get("hz"):  # (switched on: what it had is kept; a Range doesn't work with Hz bass, user)
                new["before_hz"] = {"gate": t["gate"]}
                if new.get("range"):
                    new["range_kept"] = new.pop("range")
                    new["before_hz"]["range"] = True
            return new

        placed = [t for t in tgts if t is not self.custom_defaults]
        if all(changed(t).get("hz") == t.get("hz") for t in tgts):
            return self.sync_custom()
        if hz and any(t.get("range") and not t.get("hz") for t in placed) and not messagebox.askokcancel(
                tr("panel_custom.spiderweb"), tr("panel_custom.hz_range_off"), icon="warning", parent=self):
            return self.sync_custom()
        if not self.confirm_big([changed(t) for t in placed]):
            return self.sync_custom()
        if placed:
            self.push_undo(name=tr("panel_custom.hz_bass"))
        for t in tgts:
            new = copy.deepcopy(changed(t))
            t.clear()
            t.update(new)
        self.shapes_changed()
        self.sync_custom()
        self.schedule_autosave()

    def set_hz_gates(self, mode, limit=None, every=False):
        """The gates dropdown: "mixed" (exact tone), "fixed" (every gate a whole tick) or "auto" (fixed for a tone
        held still when that's at most limit cents off, else mixed; limit None = the threshold box's, or AUTO).
        every: the notes' own gates go too, so all of them get these (the Hz bass window's dropdown, user)."""
        if mode == "auto" and limit is None:
            limit = AUTO
        want = {"fixed": True} if mode == "fixed" else {"auto": float(limit)} if mode == "auto" else {}

        def gates(hz):
            return {k: hz[k] for k in ("fixed", "auto") if k in hz}

        def own(hz):
            return every and any("gate" in n for n in hz.get("tones") or ())

        tgts = [t for t in self.custom_targets() if t.get("hz") and (gates(t["hz"]) != want or own(t["hz"]))]
        if self._loading or not tgts:
            return
        if any(t is not self.custom_defaults for t in tgts):
            self.push_undo(name=tr("panel_custom.hz_gates_step"))
        before = [auto_picks(t["hz"], self.ppq) for t in tgts]
        for t in tgts:
            t["hz"] = dict({k: v for k, v in t["hz"].items() if k not in ("fixed", "auto")}, **want)
            if own(t["hz"]):
                t["hz"]["tones"] = [{k: v for k, v in n.items() if k != "gate"} for n in t["hz"]["tones"]]
        # (a new threshold that gives every note the same gates as before leaves the notes as they are: nothing
        # to make again, and the preview keeps its sound)
        if None in before or before != [auto_picks(t["hz"], self.ppq) for t in tgts]:
            self.shapes_changed()
        self.sync_custom()
        if self.hz_window:
            self.hz_window.sync()
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
        if self.hz_window:
            self.hz_window.sync()
        self.schedule_autosave()

    def open_drawer(self):
        if self.drawer:
            self.drawer.deiconify()
            self.drawer.lift()
            return
        self.drawer = Drawer(self)
        name = self.custom_pick.get().removeprefix(MISSING_MARK) or self.custom_shape
        strokes, areas = load_drawing(name) if name else (None, [])
        if strokes:
            self.drawer.open_shape(name, strokes, areas)

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
            areas = [[u * su, v * sv, c] for u, v, c in sh.get("areas", ())]
        else:
            strokes, areas = copy.deepcopy(sh["strokes"]), copy.deepcopy(sh.get("areas", []))
        try:
            save_shape(name, join_strokes(strokes), areas)
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
