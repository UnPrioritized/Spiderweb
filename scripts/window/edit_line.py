"""The line the Stir / Warp / Liquify / Shares windows show while the colour effects after the one being edited are
skipped (Order of colour settings, PLAN.md stage C, user 2026-11-10: fully off while an earlier one is edited, worked
out again once the user leaves it): "Hidden while you edit this one: Warp, Liquify" (named, user's D check-in: a
count alone left them unsure which was which) + a Preview all button (the whole result now, without leaving the
window), and ALWAYS an "Effect order…" button beside it (user 2026-11-11: on the side panel it read like one more
effect). step_name: a colour step's name as the user sees it (the Effect order window uses it too)."""

from tkinter import ttk

from files.lang import tr
from window import look
from window.widgets import Tooltip

MOST_NAMES_CHARS = 60  # (the names cut with "…" past this many letters)


def step_name(rc, kind, n=0, sh=None):
    """The name of colour step kind ("mode", "stir", "warp", "liquify", "turned"; n = the n-th of its kind) of
    Recolour rc: "Shuffle" / "Split: Scattered" / "Split: Clumps", "Stir 2", "Warp", "Liquify", "Reverse track
    order"; "page" = shape sh's n-th note tool page as its tab shows it ("Claw 2": numbered among all its pages)."""
    if kind == "page":
        from notes.fx import pages
        ps = pages(sh.get("fx")) if sh is not None else []
        if n >= len(ps):
            return tr("order.page")
        return tr(f"{ps[n]['tool']}.tab", n=n + 1) + (tr("tool_window.off_mark") if ps[n].get("off") else "")
    if kind == "mode":
        return tr("recolour." + (rc or {}).get("mode", "shuffle"))
    if kind == "stir":
        return tr("stir.tab", n=n + 1)
    if kind == "turned":
        return tr("join_split.reverse_tracks")
    return tr(kind + ".title")


def tag_name(sh, tag):
    """The name of shape sh's step tag (kind, n) (notes/steps.py) as the user sees it: a colour effect (step_name),
    a note tool page ("Claw 2"), a flip / turn / velocities / glue after a page, Glue, Gate aware flip, Sustain,
    Gate extend, Gate subtract."""
    from notes.steps import COLOURS, FX
    kind, n = tag
    if kind in COLOURS:
        from notes.recolour import clean_recolour
        return step_name(clean_recolour(sh.get("recolour")) or {}, kind, n, sh)
    if kind == FX:
        from notes.fx import is_page
        fx = sh.get("fx") or []
        st = fx[n] if n < len(fx) else {"tool": "glue"}
        if is_page(st):
            return step_name(None, "page", sum(1 for s in fx[:n] if is_page(s)), sh)
        if st["tool"] == "turn":
            return tr("step.turn_cw" if st["deg"] > 0 else "step.turn_ccw", deg=f"{round(abs(st['deg']), 1):g}")
        if st["tool"] == "flip":
            return tr("step.flip_" + st["axis"])
        return tr("step." + st["tool"])
    if kind == "glue":
        return tr("step.glue")
    if kind == "gate_flip":
        return tr("app.gate_flip")
    return tr(kind + ".title")


def names_line(names, most=MOST_NAMES_CHARS):
    """names joined with ", ", cut with "…" past most letters (whole names only, at least one)."""
    out = ""
    for k, name in enumerate(names):
        nxt = name if not out else out + ", " + name
        if k and len(nxt) > most:
            return out + ", …"
        out = nxt
    return out


class EditLine(ttk.Frame):
    """kind = the step kind the window edits ("stir", "warp", "liquify", "mode"; "fx" = a note tool page,
    "sustain", "extend", "subtract"); after = the widget it is packed after (None: at the end). order: the Effect
    order… button (the colour effects' windows); without it the line shows only while later steps are hidden. The
    hidden line + Preview all only while later steps are hidden."""

    def __init__(self, parent, app, kind, after, wrap=280, order=True, before=None):
        super().__init__(parent)
        self.app, self.kind, self.after_w = app, kind, after
        self.label = ttk.Label(self, text="", foreground=look.HINT, wraplength=int(wrap * app.scale),
                               justify="left")
        self.label.pack(side="left", fill="x", expand=True)
        b = self.preview = ttk.Button(self, text=tr("edit.preview_all"), command=self.on_preview)
        Tooltip(b, tr("edit.preview_all_tip"))
        self.order = None
        if order:
            b = self.order = ttk.Button(self, text=tr("order.button"), command=self.on_order)
            b.pack(side="right", padx=(6, 0))
            Tooltip(b, tr("order.button_tip"))
        Tooltip(self.label, tr("edit.hidden_tip"))
        self.shown, self.text = 0, ""  # (how many later steps are hidden, the line's text)
        self.place = dict(anchor="w", fill="x", pady=(6, 0))
        if after is not None and after.winfo_manager() == "pack":
            self.place["after"] = after
        elif before is not None:
            self.place["before"] = before
        if order:
            self.pack(**self.place)

    def sync(self):
        steps = self.app.edit_hidden_steps(self.kind)
        text = ""
        if steps:
            text = tr("edit.hidden", names=names_line([tag_name(sh, t) for t, sh in steps]))
        self.shown = len(steps)
        if text == self.text:
            return
        self.text = text
        self.label.config(text=text)
        if self.order is None:  # (no Effect order…: the whole line only while something is hidden)
            if text:
                self.pack(**self.place)
            else:
                self.pack_forget()
        if text:  # (Preview all right of Effect order…: packed before it = further right)
            self.preview.pack(side="right", padx=(6, 0), **({"before": self.order} if self.order else {}))
        else:
            self.preview.pack_forget()

    def on_preview(self):
        self.app.colour_edit_leave()

    def on_order(self):
        from window.colour_order_window import open_colour_order
        open_colour_order(self.app)
