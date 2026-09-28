"""The side panel's text settings (Text tool, or text shapes selected): font, size, weight, spacing, threshold."""

import tkinter as tk
from tkinter import ttk

from files.mathexpr import calc, fmt
from notes.fonts import WEIGHTS
from notes.text import TEXT_DEFAULTS, build, restyle, shown_size, text_axes, text_font
from window.font_dialog import FontDialog
from window.widgets import Scrub, Tooltip

# number boxes: setting -> (label, unit, smallest, largest)
ENTRIES = {"size": ("Size", "", 0.01, 2000), "tracking": ("Letter spacing", "/1000 em", -1000, 10000),
           "leading": ("Line spacing", "%", 1, 1000), "threshold": ("Threshold", "%", 0, 100),
           "grow": ("Grow", "keys", -100, 100)}
# quick changes (widgets.Scrub): (step, Shift step, Ctrl step)
STEPS = {"size": (1, 10, 0.1), "tracking": (10, 100, 1), "leading": (5, 25, 1), "threshold": (1, 10, 0.1),
         "grow": (0.1, 1, 0.01)}
UNIT_CHOICES =[("font", "Font size"), ("rows", "Rows")]
ALIGN_CHOICES = [("left", "Left"), ("center", "Centre"), ("right", "Right")]
TIPS = {
    "unit": "Font size: the number is the font size in keys, like a font size anywhere\n"
            "(capital letters come out smaller than that).\n"
            "Rows: capital letters are exactly that many keys tall.\n"
            "The number stays the same when you switch, so the text changes size.",
    "tracking": "Extra room between letters, in 1/1000 of the font size (tracking).\n"
                "Minus numbers pull the letters closer together.",
    "leading": "Room between lines, in % of the font's own line spacing.",
    "threshold": "How much of a key's height has to be inside a letter for that key to play there\n"
                 "(Fill / Spam only). 50% = the key's middle. Lower = chunkier letters, higher = thinner.",
    "grow": "Makes the letters' strokes thicker, in keys (minus = thinner),\n"
            "as the text looked on screen when it was typed. Big values can look odd in tight corners.",
    "font": "Pick the font (you can type its name in the window that opens).",
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

        ttk.Label(box, text="Text", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(box, text="Font").grid(row=1, column=0, sticky="w", pady=1)
        self.font_btn = ttk.Button(box, text="Arial…", command=self.open_font_dialog)
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
            e.bind("<FocusOut>", lambda ev, key=key: self.on_text_entry(key))
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
                ttk.Label(box, text="Weight").grid(row=r, column=0, sticky="w", pady=1)
                row = ttk.Frame(box)
                row.grid(row=r, column=1, sticky="w", padx=(5, 0), pady=1)
                w = ttk.Combobox(row, textvariable=self.text_weight, state="readonly", width=10,
                                 values=[name for _, name in WEIGHTS])
                w.pack(side="left")
                w.bind("<<ComboboxSelected>>", lambda ev: self.set_text_setting(
                    {"weight": next(n for n, name in WEIGHTS if name == self.text_weight.get())}))
                ttk.Checkbutton(row, text="Italic", variable=self.text_italic,
                                command=lambda: self.set_text_setting({"italic": self.text_italic.get()})
                                ).pack(side="left", padx=(8, 0))
                r += 1
            if key == "leading":  # alignment under the spacing
                ttk.Label(box, text="Align").grid(row=r, column=0, sticky="w", pady=1)
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
        self.font_btn.config(text=f"{tx['font']}…")
        font = text_font(tx)
        if not font.found:
            info = (f"“{tx['font']}” isn't installed on this PC. The letters stay as they were saved; "
                    f"retyping uses “{font.face}” instead.")
        elif typing:
            info = "Typing: Enter = new line, Esc = done. Click somewhere else for a new text, on a text to retype it."
        elif tool == "text":
            info = "Click on the piano roll and type. Click a text to retype it."
        else:
            info = "Double-click the text (or right-click → Edit text) to retype it."
        if len(shapes) > 1 and not typing:
            info += f"  Changes go to all {len(shapes)} selected texts."
        self.text_info.config(text=info)

    def set_text_setting(self, changes, refocus=True):
        """A text setting changed in the panel: the text being typed or the selected texts get it (the first line
        stays where it starts), and new text will use it too. refocus: carry on typing on the roll."""
        if self._loading:
            return
        self.text_defaults.update({k: v for k, v in changes.items() if k in TEXT_DEFAULTS})
        roll = self.roll
        if roll.typing:
            tx, axes = roll.typing_state()
            if roll.typing["i"] is not None:
                self.push_undo(name="Text setting")
            roll.retype(*restyle(tx, axes, changes))
        else:
            shapes = self.text_shapes()
            if shapes:
                self.push_undo(name="Text setting")
            for sh in shapes:
                build(sh, *restyle(sh["text"], text_axes(sh), changes))
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
            entry.config(style="Bad.TEntry")
            return
        entry.config(style="TEntry")
        self.set_text_setting({key: value}, refocus=back)

    def open_font_dialog(self):
        if self.font_dialog and self.font_dialog.winfo_exists():
            self.font_dialog.lift()
            return
        tx, _ = self.text_current()
        self.font_dialog = FontDialog(self, tx["font"], tx.get("text", ""),
                                      lambda f: self.set_text_setting({"font": f}))
