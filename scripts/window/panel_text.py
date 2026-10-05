"""The side panel's text settings (Text tool, or text shapes selected): font, size, weight, spacing, threshold."""

import tkinter as tk
from tkinter import ttk, messagebox

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.fonts import WEIGHTS
from notes.text import TEXT_DEFAULTS, build, restyle, shown_size, text_axes, text_font, with_arial
from window.font_dialog import FontDialog
from window.widgets import Scrub, Tooltip, bad, good, leave_box

# number boxes: setting -> (label, unit, smallest, largest)
ENTRIES = {"size": (tr("panel_text.size"), "", 0.01, 2000),
           "tracking": (tr("panel_text.letter_spacing"), tr("panel_text.1000_em"), -1000, 10000),
           "leading": (tr("panel_text.line_spacing"), "%", 1, 1000),
           "threshold": (tr("panel_text.threshold"), "%", 0, 100),
           "grow": (tr("panel_text.grow"), tr("unit.keys"), -100, 100)}
# quick changes (widgets.Scrub): (step, Shift step, Ctrl step)
STEPS = {"size": (1, 10, 0.1), "tracking": (10, 100, 1), "leading": (5, 25, 1), "threshold": (1, 10, 0.1),
         "grow": (0.1, 1, 0.01)}
UNIT_CHOICES =[("font", tr("panel_text.font_size")), ("rows", tr("panel_text.rows"))]
ALIGN_CHOICES = [("left", tr("panel_text.left")), ("center", tr("panel_text.centre")),
                 ("right", tr("panel_text.right"))]
TIPS = {
    "unit": tr("panel_text.font_size_the_number_is_the"),
    "tracking": tr("panel_text.extra_room_between_letters_in_1"),
    "leading": tr("panel_text.room_between_lines_in_of_the"),
    "threshold": tr("panel_text.how_much_of_a_key_s"),
    "grow": tr("panel_text.makes_the_letters_strokes_thicker_in"),
    "font": tr("panel_text.pick_the_font_you_can_type"),
}


class TextPanel:
    """Mixed into App."""

    def _build_text(self):
        box = self.text_box = ttk.Frame(self.settings)
        box.columnconfigure(1, weight=1)
        self.text_vars = {k: tk.StringVar() for k in ENTRIES}
        self.text_entries = {}
        self._text_shown = {}  # what the number boxes showed (only a real change is applied)
        self.text_unit = tk.StringVar(value="font")
        self.text_weight = tk.StringVar()
        self.text_italic = tk.BooleanVar()
        self.text_align = tk.StringVar(value="left")
        self.font_dialog = None

        ttk.Label(box, text=tr("panel_text.text"), font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2,
                                                                                    sticky="w")
        ttk.Label(box, text=tr("panel_text.font")).grid(row=1, column=0, sticky="w", pady=1)
        self.font_btn = ttk.Button(box, text=tr("panel_text.arial"), command=self.open_font_dialog)
        self.font_btn.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=1)
        Tooltip(self.font_btn, TIPS["font"])

        r = 2
        for key in ENTRIES:
            label, unit, lo, hi = ENTRIES[key]
            lb = ttk.Label(box, text=label)
            lb.grid(row=r, column=0, sticky="w", pady=1)
            row = ttk.Frame(box)
            row.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=1)
            e = self.text_entries[key] = ttk.Entry(row, textvariable=self.text_vars[key], width=7)
            e.pack(side="left")
            e.bind("<Return>", lambda ev, key=key: self.on_text_entry(key, back=True))
            leave_box(self, e, self.text_vars[key], lambda left, key=key: self.on_text_entry(key))
            Scrub(self, [(e, self.text_vars[key], lambda key=key: self.on_text_entry(key))], STEPS[key], lo, hi,
                  label=lb)
            if unit:
                ttk.Label(row, text=unit, foreground="#777").pack(side="left", padx=(3, 0))
            if key in TIPS:
                Tooltip(e, TIPS[key])
            if key == "size":
                for value, text in UNIT_CHOICES:
                    b = ttk.Radiobutton(row, text=text, value=value, variable=self.text_unit, style="Toolbutton",
                                        command=lambda: self.set_text_setting({"unit": self.text_unit.get()}))
                    b.pack(side="left", padx=(4 if value == "font" else 1, 0))
                    Tooltip(b, TIPS["unit"])
            r += 1
            if key == "size":  # weight and italic under the size
                ttk.Label(box, text=tr("panel_text.weight")).grid(row=r, column=0, sticky="w", pady=1)
                row = ttk.Frame(box)
                row.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=1)
                w = ttk.Combobox(row, textvariable=self.text_weight, state="readonly", width=10,
                                 values=[name for _, name in WEIGHTS])
                w.pack(side="left")
                w.bind("<<ComboboxSelected>>", lambda ev: self.set_text_setting(
                    {"weight": next(n for n, name in WEIGHTS if name == self.text_weight.get())}))
                ttk.Checkbutton(row, text=tr("panel_text.italic"), variable=self.text_italic,
                                command=lambda: self.set_text_setting({"italic": self.text_italic.get()})
                                ).pack(side="left", padx=(8, 0))
                r += 1
            if key == "leading":  # alignment under the spacing
                ttk.Label(box, text=tr("panel_text.align")).grid(row=r, column=0, sticky="w", pady=1)
                row = ttk.Frame(box)
                row.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=1)
                for value, text in ALIGN_CHOICES:
                    ttk.Radiobutton(row, text=text, value=value, variable=self.text_align, style="Toolbutton",
                                    command=lambda: self.set_text_setting({"align": self.text_align.get()})
                                    ).pack(side="left", padx=(0, 1))
                r += 1
        self.text_info = ttk.Label(box, text="", foreground="#777", font=("Segoe UI", 8),
                                   wraplength=int(300 * self.scale), justify="left")
        self.text_info.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0))

    # ------------------------------------------------------------ what the panel shows / changes

    def text_shapes(self):
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i].get("text")]

    def text_current(self):
        """(settings, size box number) of what the panel shows: the text being typed, the selected text, or the
        settings for new text."""
        if self.roll.typing:
            tx, axes = self.roll.typing_state()
            return tx, shown_size(tx, axes)
        shapes = self.text_shapes()
        if shapes:
            return shapes[0]["text"], shown_size(shapes[0]["text"], text_axes(shapes[0]))
        return self.text_defaults, self.text_defaults["size"]

    def sync_text(self):
        typing = self.roll.typing
        tool = self.tool.get()
        shapes = self.text_shapes()
        self._rows["text"] = bool(typing or shapes or tool == "text")
        self.layout_rows()
        if not self._rows["text"]:
            return
        tx, size = self.text_current()
        self._loading = True
        for key, var in self.text_vars.items():
            value = size if key == "size" else tx[key]
            var.set(fmt(round(value, 3)))
            self._text_shown[key] = var.get()
            self.text_entries[key].config(style="TEntry")
        self.text_unit.set(tx["unit"])
        self.text_weight.set(min(WEIGHTS, key=lambda w: abs(w[0] - tx["weight"]))[1])
        self.text_italic.set(tx["italic"])
        self.text_align.set(tx["align"])
        self._loading = False
        self.font_btn.config(text=tr("panel_text.text_2", font=tx['font']))
        font = text_font(tx)
        if not font.found:
            info = tr("panel_text.isn_t_installed_on_this_pc", font=tx['font'])
        elif typing:
            info = tr("panel_text.typing_enter_new_line_esc_done")
        elif tool == "text":
            info = tr("panel_text.click_on_the_piano_roll_and")
        else:
            info = tr("panel_text.double_click_the_text_or_right")
        if len(shapes) > 1 and not typing:
            info += tr("panel_text.changes_go_to_all_selected_texts", n=len(shapes))
        self.text_info.config(text=info)

    def missing_font_ok(self, txs):
        """These placed texts are about to be redrawn: if a font of theirs isn't installed here, ask first (they're
        redrawn in Arial, with_arial). True = go on."""
        missing = sorted({tx["font"] for tx in txs if not text_font(tx).found})
        return not missing or messagebox.askyesno(
            tr("app.spiderweb_2"), tr("panel_text.font_missing_edit", fonts=", ".join(f"“{f}”" for f in missing)),
            icon="warning", parent=self)

    def set_text_setting(self, changes, refocus=True):
        """A text setting changed in the panel: the text being typed or the selected texts get it (the first line
        stays where it starts), and new text will use it too. refocus: carry on typing on the roll."""
        if self._loading:
            return
        roll = self.roll
        placed = ([roll.typing_state()[0]] if roll.typing["i"] is not None else []) if roll.typing else \
            [sh["text"] for sh in self.text_shapes()]
        if "font" not in changes and not self.missing_font_ok(placed):
            self.sync_text()  # (the panel shows the old settings again)
            return
        self.text_defaults.update({k: v for k, v in changes.items() if k in TEXT_DEFAULTS})
        if roll.typing:
            tx, axes = roll.typing_state()
            if roll.typing["i"] is not None:
                self.push_undo(name=tr("panel_text.text_setting"))
            roll.retype(*restyle(tx, axes, with_arial(tx, changes)))
        else:
            shapes = self.text_shapes()
            if shapes:
                self.push_undo(name=tr("panel_text.text_setting"))
            for sh in shapes:
                build(sh, *restyle(sh["text"], text_axes(sh), with_arial(sh["text"], changes)))
        self.shapes_changed()
        self.sync_text()
        self.sync_custom()
        if roll.typing and refocus:
            roll.focus_set()  # carry on typing

    def on_text_entry(self, key, back=False):
        var, entry = self.text_vars[key], self.text_entries[key]
        if self._loading or var.get() == self._text_shown.get(key):
            if back and self.roll.typing:
                self.roll.focus_set()
            return
        _, _, lo, hi = ENTRIES[key]
        try:
            value = float(calc(var.get()))
            if not lo <= value <= hi:
                raise ValueError
        except ValueError:
            bad(entry)
            return
        good(entry)
        self.set_text_setting({key: value}, refocus=back)

    def open_font_dialog(self):
        if self.font_dialog and self.font_dialog.winfo_exists():
            self.font_dialog.lift()
            return
        tx, _ = self.text_current()
        self.font_dialog = FontDialog(self, tx["font"], tx.get("text", ""),
                                      lambda f: self.set_text_setting({"font": f}))
