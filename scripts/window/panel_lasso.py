"""The side panel's Lasso box, shown while the Lasso tool is picked (user 2026-10-22, planned-features.md "Lasso
slice"): Cut / Cancel for the loop (= Enter / Esc) and the Magnetic lasso's Width."""

from tkinter import TclError, ttk

from files.lang import tr
from files.mathexpr import calc
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, unchanged


class LassoPanel:
    """Mixed into App."""

    def _build_lasso(self, side):
        box = self.lasso_box = ttk.LabelFrame(side, text=tr("app.lasso"), padding=6)
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.lasso_cut_btn = ttk.Button(row, text=tr("panel_lasso.cut"), command=self.roll_lasso_enter)
        self.lasso_cut_btn.pack(side="left")
        self.lasso_cancel_btn = ttk.Button(row, text=tr("panel_lasso.cancel"), command=lambda: self.roll.drop_lasso())
        self.lasso_cancel_btn.pack(side="left", padx=4)
        self.lasso_hint = ttk.Label(row, text=tr("panel_lasso.none"), foreground=look.HINT)
        Tooltip(self.lasso_cut_btn, tr("panel_lasso.cut_tip"))
        Tooltip(self.lasso_cancel_btn, tr("panel_lasso.cancel_tip"))
        w = self.lasso_width_row = ttk.Frame(box)
        lb = ttk.Label(w, text=tr("panel_lasso.width"))
        lb.pack(side="left")
        e = self.lasso_width_entry = ttk.Entry(w, textvariable=self.lasso_width, width=5)
        e.pack(side="left", padx=(5, 3))
        e.bind("<Return>", lambda ev: self.on_lasso_width())
        leave_box(self, e, self.lasso_width, self.on_lasso_width)
        self.lasso_scrub = Scrub(self, [(e, self.lasso_width, self.on_lasso_width)], (1, 10, 1), 1, 256, label=lb)
        ttk.Label(w, text=tr("panel_lasso.width_hint"), foreground=look.HINT).pack(side="left")
        for x in (lb, e):
            Tooltip(x, tr("panel_lasso.width_tip"))
        good(e)  # (a wrong first value goes back to this one, not left red; hunt 10-23; tool_picker's restore too)
        self._lasso_shown = None

    def lasso_px(self):
        """The Magnetic Width (1-256 px; the box half typed: the last good one)."""
        try:
            self._lasso_px = min(256, max(1, int(round(float(self.lasso_width.get())))))
        except (TclError, ValueError, OverflowError):  # (half typed, "inf", 1e999: the last good one)
            pass
        return getattr(self, "_lasso_px", 10)

    def roll_lasso_enter(self):
        self.roll.lasso_enter()
        self.roll.focus_set()  # (the keys back to the piano roll: Enter / Esc go on working there)

    def sync_lasso(self):
        """Shown with the Lasso tool only; Cut / Cancel greyed with no loop; Width with Magnetic only."""
        if not hasattr(self, "lasso_box"):
            return
        on = self.tool.get() == "lasso"
        magnet = self.lasso_mode.get() == "magnet"
        las = self.roll.lasso if hasattr(self, "roll") else None
        state = (on, magnet, bool(las), bool(las and las["closed"]))
        if state == self._lasso_shown:
            return  # (packing again anyway makes the panel flash)
        self._lasso_shown = state
        if not on:
            self.lasso_box.pack_forget()
            return
        self.lasso_box.pack(fill="x", pady=(8, 0), before=self.settings)
        self.lasso_cut_btn.state(["!disabled"] if las else ["disabled"])
        self.lasso_cancel_btn.state(["!disabled"] if las else ["disabled"])
        if las:
            self.lasso_hint.pack_forget()
        else:
            self.lasso_hint.pack(side="left", padx=(4, 0))
        if magnet:
            self.lasso_width_row.pack(fill="x", pady=(4, 0))
        else:
            self.lasso_width_row.pack_forget()

    def on_lasso_width(self, left=False):
        """The Width box (Enter, stepped, or left: widgets.leave_box)."""
        if left and unchanged(self.lasso_width_entry):
            return
        try:
            value = calc(str(self.lasso_width_entry.get()))
            if not 1 <= value <= 256:
                raise ValueError
        except (ValueError, OverflowError, ZeroDivisionError):
            bad(self.lasso_width_entry)
            return
        good(self.lasso_width_entry)
        self.lasso_width.set(str(int(round(value))))
        self.roll.draw_lasso()
