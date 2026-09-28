"""Piano roll: the right-click menu for shapes (and a funnel's highlighted lines / curves)."""

import tkinter as tk
from types import SimpleNamespace

from window.curve_dialog import load_formulas
from notes.joined import is_joined
from notes.funnel import CURVE_PRESETS, inside_out, turned_curve
from notes.tumour import LINE_KINDS
from roll.roll_shared import SHIFT
from window.widgets import symmetry_menu


class ShapeMenu:
    """Mixed into PianoRoll."""

    def show_menu(self, e, i):
        """Right-click (no drag) near shape i: it gets selected (unless it already is), then the menu."""
        app = self.app
        if i not in app.sels:
            app.select(i)
        sh = app.shapes[i]
        # where it was clicked, for "... here" (Shift held on the right-click: exactly there, not snapped)
        at = SimpleNamespace(x=e.x, y=e.y, state=e.state & SHIFT)
        part = None
        if sh["kind"] == "funnel" and app.sels == {i}:
            part = self.part_at(sh, e.x, e.y)
            if part and part not in app.parts:
                app.set_parts(self.part_group(sh, part), main=part)
        m = tk.Menu(self, tearoff=0)
        got = self.funnel_parts()
        if got:
            _, lines, curves = got
            m.add_command(label=f"Highlighted: {self.parts_text()}", state="disabled")
            if curves:
                presets = tk.Menu(m, tearoff=0)
                for name, text in CURVE_PRESETS:
                    presets.add_command(label=name, command=lambda t=text: self.apply_formula(t))
                m.add_cascade(label="Curve shape", menu=presets)
                mine = tk.Menu(m, tearoff=0)
                for name, text in load_formulas():
                    mine.add_command(label=f"{name}   ({text})", command=lambda t=text: self.apply_formula(t))
                if mine.index("end") is not None:
                    mine.add_separator()
                mine.add_command(label="New / edit formulas…", command=self.open_formulas)
                m.add_cascade(label="Custom formula", menu=mine)
                self.link_menu(m, sh, curves, part)
                m.add_command(label="Turn curve end to end", accelerator="Ctrl+H",
                              command=lambda: self.set_curves(turned_curve))
                m.add_command(label="Flip curve inside out", accelerator="Ctrl+J",
                              command=lambda: self.set_curves(inside_out))
                m.add_command(label="Copy curve shape", accelerator="Ctrl+C", command=self.copy_curve)
                m.add_command(label="Paste curve shape", accelerator="Ctrl+V", command=self.paste_curve,
                              state="normal" if self.curve_clip else "disabled")
            what = "lines and curves" if lines and curves else "lines" if lines else "curves"
            m.add_command(label=f"Delete highlighted {what}", accelerator="Del", command=self.delete_parts)
            m.add_command(label="Clear highlight", accelerator="Esc", command=lambda: app.set_parts(()))
            m.add_separator()
        keys = not got  # with something highlighted, the shortcut keys work on that instead
        curve_keys = not (got and got[2])

        def item(label, key, fn, on=True, keys=keys):
            m.add_command(label=label, accelerator=key if keys else "", command=fn,
                          state="normal" if on else "disabled")

        if sh["kind"] == "funnel" and len(sh["pts"]) >= 4 and len(app.sels) == 1:
            on_line = self.on_funnel_line(sh, at) is not None
            item("Add curve start here" if on_line else "Add anchor here", "", lambda: self.funnel_click(sh, at),
                 on_line or bool(sh["starts"]))
        if sh["kind"] == "poly" and len(app.sels) == 1:
            item("Add point here", "", lambda: self.insert_poly_point(sh, at))
        if sh["kind"] == "curve" and len(app.sels) == 1:
            item("Add anchor here", "", lambda: self.curve_click(sh, at))
            if not is_joined(sh):  # one half follows the other; the half right-clicked keeps its shape
                symmetry_menu(m, sh.get("sym"), lambda mode: self.set_symmetry(sh, mode, at))
        if sh.get("text") and len(app.sels) == 1:
            item("Edit text", "", lambda: self.edit_text(at))
        if sh["kind"] == "custom" and "notes" not in sh and len(app.sels) == 1:
            k = None if sh.get("text") else self.stroke_at(sh, e.x, e.y)
            if k is not None:  # the stroke right-clicked gets picked
                app.set_stroke(k)
                if sh["strokes"][k]["kind"] == "curve":
                    item("Add anchor here", "", lambda: self.stroke_click(sh, at))
                    symmetry_menu(m, sh["strokes"][k].get("sym"), lambda mode: self.stroke_symmetry(sh, mode, at))
                item("Delete this stroke", "Del", lambda: self.delete_stroke(sh, k), keys=True)
            item("Save drawing to the shape library…", "", lambda: app.save_to_library(sh))
        if app.tumour_targets():
            item("Tumours…", "", app.open_tumours)
        if len(app.sels) >= 2:  # (greyed out, saying why, when something else is selected too)
            ok = app.can_join()
            item("Join shapes into one curve" if ok else "Join shapes into one curve  (only lines, polylines, "
                 "freehand strokes, curves and arcs)", "", app.join_selected, ok)
        if len(app.sels) == 1 and sh["kind"] in LINE_KINDS:
            item("Split here", "", lambda: app.split_here(i, at))
        if len(app.sels) == 1 and app.can_split_pieces(sh):
            item("Split into separate shapes", "", lambda: app.split_pieces(i))
        n = len(app.sels)
        shapes = "shape" if n == 1 else f"{n} shapes"
        item(f"Delete {shapes}", "Del", app.delete_selected, keys=keys and self.picked_stroke(sh) is None)
        item(f"Duplicate {shapes}", "Ctrl+D", app.duplicate, keys=True)
        item(f"Copy {shapes}", "Ctrl+C", lambda: app.copy_selected(whole=True), keys=curve_keys)
        item("Paste at the play line", "Ctrl+V", lambda: app.paste(whole=True), bool(app.clipboard), keys=curve_keys)
        m.add_separator()
        item("Flip sideways", "Ctrl+H", lambda: app.flip(True, whole=True), keys=curve_keys)
        item("Flip upside down", "Ctrl+J", lambda: app.flip(False, whole=True), keys=curve_keys)
        item("Turn 90° left", "Ctrl+Left", lambda: app.rotate(False), keys=True)
        item("Turn 90° right", "Ctrl+Right", lambda: app.rotate(True), keys=True)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def link_menu(self, m, sh, curves, part):
        """"Link curves ▸" for the highlighted curves. The curve right-clicked keeps its shape, the others follow."""
        source = part[1:] if part and part[0] == "curve" and part[1:] in curves else min(curves)
        others = sorted(curves - {source})
        sub = tk.Menu(m, tearoff=0)
        if others:
            look = self.link_relation(sh, source, others[0])
            closest = "Flipped" if self.closest_link(sh, source, others[0]) else look
            sub.add_command(label=f"Auto (closest now: {closest})",
                            command=lambda: self.link_curves("auto", source))
            sub.add_command(label=look, command=lambda: self.link_curves("same", source))
            sub.add_command(label="Flipped (turned end to end)", command=lambda: self.link_curves("flip", source))
            sub.add_separator()
        linked = any(sh["starts"][k]["ends"][end].get("link") is not None for k, end in curves)
        sub.add_command(label="Unlink", command=lambda: self.link_curves(None, source),
                        state="normal" if linked else "disabled")
        m.add_cascade(label="Link curves", menu=sub)
