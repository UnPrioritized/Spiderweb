"""The side panel's tumour line for lines, polylines, freehand strokes, curves and arcs (tumour.py): a short summary
and a button that opens the tumour window (tumour_window.py), where the settings are."""

from tkinter import ttk

from files.lang import tr
from notes.joined import shown_tumour
from notes.tumour import LINE_KINDS
from window.tumour_window import SHAPE_CHOICES, TumourWindow
from window.widgets import Tooltip


class TumourPanel:
    """Mixed into App."""

    def _build_tumour(self):
        box = self.tumour_box = ttk.Frame(self.settings)
        self.tumour_window = None
        self.tumour_pos = ""  # where the tumour window was last ("+x+y", remembered in the autosave)
        self.graph_pos = ""   # the same for the graph window (graph_window.py)
        self.tumour_btn = ttk.Button(box, text=tr("panel_tumour.tumours"), command=self.open_tumours)
        self.tumour_btn.pack(side="left")
        Tooltip(self.tumour_btn, tr("panel_tumour.bumps_along_the_line_opens_the"))
        self.tumour_summary = ttk.Label(box, text="", foreground="#777")
        self.tumour_summary.pack(side="left", padx=(8, 0))

    def tumour_targets(self):
        """What the tumour settings change: the selected lines / polylines / freehand strokes / curves / arcs."""
        return [self.shapes[i] for i in sorted(self.sels) if self.shapes[i]["kind"] in LINE_KINDS]

    def shown_tumours(self):
        """The tumour settings the tumour window shows: the first selected line's that has tumours on (else the first
        one's that has any), None when none of them has any."""
        tms = [tm for tm in map(shown_tumour, self.tumour_targets()) if tm]
        return next((tm for tm in tms if tm.get("on")), tms[0] if tms else None)

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
        on = [shown_tumour(t) for t in tgts if (shown_tumour(t) or {}).get("on")]
        if any(t.get("tumours") for t in tgts):
            text = tr("panel_tumour.each_joined_shape_has_its_own")
        elif not on:
            text = tr("panel_tumour.no_tumours")
        elif len(tgts) > 1:
            text = tr("panel_tumour.tumours_on_of", n=len(on), n2=len(tgts))
        else:
            text = tr("panel_tumour.keys", dict=dict(SHAPE_CHOICES)[on[0]['shape']], size=round(on[0]['size'], 2))
            if on[0].get("graphs"):
                text += tr("panel_tumour.with_graphs")
        self.tumour_summary.config(text=text)
