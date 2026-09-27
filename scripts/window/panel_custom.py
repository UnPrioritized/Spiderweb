"""The side panel's custom shape settings (which shape, how it's filled) and the drawer window."""

import copy
import math
from tkinter import ttk, messagebox, simpledialog

from notes.custom import SPAM_FILLS, box_frame, join_strokes, map_stroke, normalize_strokes, open_paths
from window.drawer import Drawer, clean_name, library_names, load_shape, save_shape
from window.panel_funnel import GATE_STEPS
from files.mathexpr import calc, fmt
from roll.roll_live import BOX_TOOLS, STROKE_TOOLS
from window.widgets import Scrub, Tooltip

GAP_COLOR = "#c06000"  # Fill / Spam on a shape whose outline has one gap (closed with a straight line)
ALIGN_CHOICES = [
    ("auto", "Auto", "Each key's notes start at that key's left edge,\nso the left side is exact and the right side ragged."),
    ("aligned", "Aligned", "Every note sits on the gate grid counted from the start of the song,\n"
                           "so the notes form straight columns (lined up with bar lines and\n"
                           "other shapes); both sides are a little ragged."),
]


class CustomPanel:
    """Mixed into App."""

    def _build_custom(self):
        """Custom shape settings: which library shape, and how the inside is filled."""
        box = self.custom_box = ttk.Frame(self.settings)
        row = self.custom_shape_row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="Shape").pack(side="left")
        self.custom_combo = ttk.Combobox(row, textvariable=self.custom_pick, state="readonly", width=18,
                                         postcommand=self.refresh_custom_names)
        self.custom_combo.pack(side="left", padx=(5, 0))
        self.custom_combo.bind("<<ComboboxSelected>>", lambda e: self.on_custom_pick())
        ttk.Button(row, text="Drawer…", command=self.open_drawer).pack(side="left", padx=(4, 0))
        row = self.custom_fill_row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Inside").pack(side="left", anchor="n")
        opts = ttk.Frame(row)
        opts.pack(side="left", padx=(5, 0))
        self.fill_buttons = {}
        self.fill_tips = {}  # Fill / Spam: why they're orange (one gap) or greyed out (more)
        ttk.Style(self).configure("Gap.TRadiobutton", foreground=GAP_COLOR)
        for value, text in (("empty", "Empty (outline only)"), ("fill", "Fill (one long note per key)"),
                            ("spam", "Spam (notes of one gate)"),
                            ("outline_spam", "Outline spam (the outline in notes of one gate)")):
            b = ttk.Radiobutton(opts, text=text, value=value, variable=self.fill_var,
                                command=lambda: self.set_custom("fill", self.fill_var.get()))
            b.pack(anchor="w")
            self.fill_buttons[value] = b
            if value in ("fill", "spam"):
                self.fill_tips[value] = Tooltip(b, "")
        g = ttk.Frame(opts)
        g.pack(anchor="w", padx=(20, 0))
        lb = ttk.Label(g, text="gate")
        lb.pack(side="left")
        self.gate_entry = ttk.Entry(g, textvariable=self.gate_var, width=7)
        self.gate_entry.pack(side="left", padx=4)
        ttk.Label(g, text="ticks (Enter to apply)", foreground="#777").pack(side="left")
        self.gate_entry.bind("<Return>", lambda e: self.on_gate())
        self.gate_entry.bind("<FocusOut>", lambda e: self.on_gate())
        Scrub(self, [(self.gate_entry, self.gate_var, self.on_gate)], GATE_STEPS, 1, 10 ** 7, label=lb)
        a = ttk.Frame(opts)
        a.pack(anchor="w", padx=(20, 0), pady=(1, 0))
        ttk.Label(a, text="start").pack(side="left")
        self.align_buttons = []
        for value, text, tip in ALIGN_CHOICES:
            b = ttk.Radiobutton(a, text=text, value=value, variable=self.align_var,
                                command=lambda: self.set_custom("align", self.align_var.get()))
            b.pack(side="left", padx=(4, 0))
            Tooltip(b, tip)
            self.align_buttons.append(b)
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
                    fill=self.custom_defaults["fill"], gate=self.custom_defaults["gate"],
                    align=self.custom_defaults["align"],
                    pts=box_frame(b0, p0, b1, p1))

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
                f"{sum(self.note_count(t) for t in tgts):,} pasted notes. "
                + ("They keep their own velocities until you change the velocity here or in the velocity pane. "
                   if own else "")
                + "Drag a corner or side to stretch them, just outside a corner to turn them, just outside a "
                  "side's middle to skew them."))
            return
        # gaps in the outline (open lines once touching strokes are joined): one is closed with a straight line
        # for Fill / Spam, with more it's unclear what's inside
        if placed:
            name, gaps = tgts[0]["name"], max(len(open_paths(t["strokes"])) for t in tgts)
        elif tool != "custom":  # drawn on the roll: the fill settings are for what gets drawn
            name, gaps = "Live drawing" if live else tool.title(), 0
        else:
            name, tpl = self.custom_shape, self.custom_template(self.custom_shape)
            gaps = len(open_paths(tpl[0])) if tpl else 2
        fillable = gaps <= 1
        fill, gate = tgts[0]["fill"], tgts[0]["gate"]
        self._loading = True
        self.custom_pick.set(name)
        self.fill_var.set(fill if fillable or fill == "outline_spam" else "empty")
        self.gate_var.set(fmt(round(gate * self.ppq, 3)))
        self.align_var.set(tgts[0].get("align", "auto"))
        self.gate_entry.config(style="TEntry")
        self._loading = False
        for value, b in self.fill_buttons.items():
            b.config(state="normal" if fillable or value in ("empty", "outline_spam") else "disabled",
                     style="Gap.TRadiobutton" if gaps == 1 and value in self.fill_tips else "TRadiobutton")
        for tip in self.fill_tips.values():
            tip.text = ("The outline has a gap: it's filled as if a straight line closed it (the dashed line).\n"
                        "Close the gap yourself to decide where the edge goes." if gaps == 1 else
                        f"The outline has {gaps} gaps, so it's unclear what's inside.\n"
                        "Close all but one of them to fill it." if gaps else "")
        spam = fill in SPAM_FILLS and (fillable or fill == "outline_spam")
        self.gate_entry.config(state="normal" if spam else "disabled")
        for b in self.align_buttons:
            b.config(state="normal" if spam else "disabled")
        if not placed and live:
            info = ("Live shape: what you draw goes into one custom shape (a new one now). Close its outline "
                    "(points snap onto its ends) to fill it.")
        elif not placed and tool == "text":
            info = "Inside fill for new text."
        elif not placed and tool in BOX_TOOLS:
            info = f"Drag a box on the piano roll, or click two corners (Ctrl = a perfect {tool} on screen)."
        elif not placed and not self.custom_template(name):
            info = "Pick a shape, or make one with Drawer…"
        elif not fillable:
            info = (f"This shape's outline has {gaps} gaps, so only Empty and Outline spam work. Close all but one "
                    "to fill it.")
        elif placed:
            info = f"{sum(self.note_count(t) for t in tgts):,} notes."
            if gaps and fill in ("fill", "spam"):
                info += "  One gap in the outline: filled as if the dashed line closed it."
        else:
            info = "Drag a box on the piano roll, or click two corners, to place it (Ctrl = keep its proportions)."
        if placed and tool != "text":
            info += ("  Drag a corner or side to resize, just outside a corner to turn it, just outside a side's "
                     "middle to skew it.")
            if live and len(tgts) == 1:
                info += "  Live shape: what you draw now goes into this shape."
            if self.stroke is not None and len(tgts) == 1:
                info += (f"  Picked: stroke {self.stroke + 1} of {len(tgts[0]['strokes'])} (Del = delete it, "
                         "Esc = unpick). Select tool: click a stroke of the selected shape to pick it.")
        self.custom_info.config(text=info)

    def on_custom_pick(self):
        """A shape picked in the box: used for new custom shapes, and swapped into the selected ones."""
        name = self.custom_pick.get()
        tpl = self.custom_template(name)
        if not tpl:
            messagebox.showerror("Spiderweb", f"Couldn't read the shape \"{name}\".")
            return self.sync_custom()
        self.custom_shape = name
        tgts = [t for t in self.custom_targets()
                if t is not self.custom_defaults and not t.get("text") and "notes" not in t]
        if tgts:
            self.push_undo()
            for t in tgts:
                t["name"], t["strokes"] = name, copy.deepcopy(tpl[0])
            self.shapes_changed()
        self.sync_custom()
        self.schedule_autosave()

    def set_custom(self, key, value):
        """A custom shape setting (fill / gate / align) changed in the panel."""
        if self._loading:
            return
        tgts = self.custom_targets()
        placed = [t for t in tgts if t is not self.custom_defaults]
        same = all(abs(t[key] - value) < 1e-12 if key == "gate" else t.get(key) == value for t in tgts)
        if same or not self.confirm_big([dict(t, **{key: value}) for t in placed]):
            return self.sync_custom()
        if placed:
            self.push_undo()
        for t in tgts:
            t[key] = value
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
        name = clean_name(simpledialog.askstring("Spiderweb", "Save the drawing to the shape library as:",
                                                 initialvalue="" if sh["name"] == "Live drawing" else sh["name"],
                                                 parent=self) or "")
        if not name:
            return
        if name.lower() in (n.lower() for n in library_names()) and not messagebox.askyesno(
                "Spiderweb", f"\"{name}\" is already in the library. Replace it?\n"
                "Shapes already placed on the piano roll stay as they are.", parent=self):
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
            messagebox.showerror("Spiderweb", f"Couldn't save:\n{e}", parent=self)
            return
        sh["name"] = name
        self.refresh_custom_names()
        if self.drawer:
            self.drawer.refresh_list()
        self.shapes_changed()
        self.sync_custom()
        self.status.config(text=f"Saved \"{name}\" to the shape library.")

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
