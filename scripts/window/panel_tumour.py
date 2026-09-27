"""The side panel's tumour line for lines, polylines, freehand strokes, curves and arcs (tumour.py): a short summary
and a button that opens the tumour window (tumour_window.py), where the settings are."""

from tkinter import ttk

from notes.tumour import LINE_KINDS
from window.tumour_window import SHAPE_CHOICES, TumourWindow
from window.widgets import Tooltip


class TumourPanel:
    """Mixed into App."""

    def _build_tumour(self):
        box = self.tumour_box = ttk.Frame(self.settings)
        self.tumour_window = None
        self.tumour_pos = ""  # where the tumour window was last ("+x+y", remembered in the autosave)
        self.tumour_btn = ttk.Button(box, text="Tumours…", command=self.open_tumours)
        self.tumour_btn.pack(side="left")
        Tooltip(self.tumour_btn, "Bumps along the line: opens the tumour window.\n"
                                 "Also in the right-click menu (Tumours…).")
        self.tumour_summary = ttk.Label(box, text="", foreground="#777")
        self.tumour_summary.pack(side="left", padx=(8, 0))

    def tumour_targets(self):
        """What the tumour settings change: the selected lines / polylines / freehand strokes / curves / arcs."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] in LINE_KINDS]

    def open_tumours(self):
        if self.tumour_window:
            self.tumour_window.deiconify()
            self.tumour_window.lift()
        else:
            self.tumour_window = TumourWindow(self)
        self.tumour_window.focus_set()

    def sync_tumour(self):
        self.sync_tumour_summary()
        if self.tumour_window:
            self.tumour_window.sync()

    def sync_tumour_summary(self):
        tgts = self.tumour_targets()
        self._rows["tumour"] = bool(tgts)
        self.layout_rows()
        if not tgts:
            return
        on = [t["tumour"] for t in tgts if (t.get("tumour") or {}).get("on")]
        if not on:
            text = "No tumours"
        elif len(tgts) > 1:
            text = f"Tumours on {len(on)} of {len(tgts)}"
        else:
            text = f"{dict(SHAPE_CHOICES)[on[0]['shape']]}, {round(on[0]['size'], 2):g} keys"
        self.tumour_summary.config(text=text)
