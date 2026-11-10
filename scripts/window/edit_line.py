"""The line the Stir / Warp / Liquify / Shares windows show while the colour effects after the one being edited are
skipped (Order of colour settings, PLAN.md stage C, user 2026-11-10: fully off while an earlier one is edited, worked
out again once the user leaves it): "2 later effects hidden while you edit this one" + a Preview all button (the
whole result now, without leaving the window)."""

from tkinter import ttk

from files.lang import tr, trn
from window import look
from window.widgets import Tooltip


class EditLine(ttk.Frame):
    """kind = the step kind the window edits ("stir", "warp", "liquify", "mode"); after = the widget it is packed
    after while shown."""

    def __init__(self, parent, app, kind, after, wrap=280):
        super().__init__(parent)
        self.app, self.kind, self.after_w = app, kind, after
        self.label = ttk.Label(self, text="", foreground=look.HINT, wraplength=int(wrap * app.scale),
                               justify="left")
        self.label.pack(side="left", fill="x", expand=True)
        b = ttk.Button(self, text=tr("edit.preview_all"), command=self.on_preview)
        b.pack(side="right", padx=(6, 0))
        Tooltip(b, tr("edit.preview_all_tip"))
        Tooltip(self.label, tr("edit.hidden_tip"))
        self.shown = 0

    def sync(self):
        n = self.app.edit_hidden_count(self.kind)
        if n == self.shown:
            return
        self.shown = n
        if n:
            self.label.config(text=trn("edit.hidden", n, n=n))
            self.pack(anchor="w", fill="x", pady=(6, 0), after=self.after_w)
        else:
            self.pack_forget()

    def on_preview(self):
        self.app.colour_edit_leave()
