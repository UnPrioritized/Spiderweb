"""Piano roll: the right-click menu for shapes (and a funnel's highlighted lines / curves)."""

import tkinter as tk
from types import SimpleNamespace

from files.lang import tr
from window.formula_host import FunnelHost, PolygonHost, RollHost, formula_menu
from notes.convert import originals
from notes.joined import is_joined
from notes.funnel import inside_out, turned_curve
from notes.tumour import LINE_KINDS
from roll.roll_shared import SHIFT
from window.claw_window import open_claw
from window.strum_window import open_strum
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
            m.add_command(label=tr("roll_menu.highlighted", parts_text=self.parts_text()), state="disabled")
            if curves:
                self.formula_menu(m, FunnelHost(self))
                m.add_command(label=tr("roll_menu.default_curve"), command=self.default_curves)
                self.link_menu(m, sh, curves, part)
                m.add_command(label=tr("roll_menu.turn_curve_end_to_end"), accelerator=tr("roll_menu.ctrl_h"),
                              command=lambda: self.set_curves(turned_curve))
                m.add_command(label=tr("roll_menu.flip_curve_inside_out"), accelerator=tr("roll_menu.ctrl_j"),
                              command=lambda: self.set_curves(inside_out))
                m.add_command(label=tr("roll_menu.copy_curve_shape"), accelerator=tr("roll_menu.ctrl_c"),
                              command=self.copy_curve)
                m.add_command(label=tr("roll_menu.paste_curve_shape"), accelerator=tr("roll_menu.ctrl_v"),
                              command=self.paste_curve,
                              state="normal" if self.curve_clip else "disabled")
            what = tr("roll_menu.lines_and_curves") if lines and curves else "lines" if lines else "curves"
            m.add_command(label=tr("roll_menu.delete_highlighted", what=what), accelerator=tr("roll_menu.del"),
                          command=self.delete_parts)
            m.add_command(label=tr("roll_menu.clear_highlight"), accelerator=tr("roll_menu.esc"),
                          command=lambda: app.set_parts(()))
            m.add_separator()
        keys = not got  # with something highlighted, the shortcut keys work on that instead
        curve_keys = not (got and got[2])

        def item(label, key, fn, on=True, keys=keys):
            m.add_command(label=label, accelerator=key if keys else "", command=fn,
                          state="normal" if on else "disabled")

        if sh["kind"] == "funnel" and len(sh["pts"]) >= 4 and len(app.sels) == 1:
            on_line = self.on_funnel_line(sh, at) is not None
            item(tr("roll_menu.add_curve_start_here") if on_line else tr("roll_menu.add_anchor_here"), "",
                 lambda: self.funnel_click(sh, at),
                 on_line or bool(sh["starts"]))
        if sh["kind"] == "poly" and len(app.sels) == 1:
            item(tr("roll_menu.add_point_here"), "", lambda: self.insert_poly_point(sh, at))
        if sh["kind"] == "curve" and len(app.sels) == 1:
            item(tr("roll_menu.add_anchor_here"), "", lambda: self.curve_click(sh, at))
            if not is_joined(sh):  # one half follows the other; the half right-clicked keeps its shape
                symmetry_menu(m, sh.get("sym"), lambda mode: self.set_symmetry(sh, mode, at))
        if app.pattern_targets():
            self.formula_menu(m)
        elif app.polygon_shapes():  # (a shape / pattern on the sides)
            self.formula_menu(m, PolygonHost(app))
        if sh.get("text") and len(app.sels) == 1:
            item(tr("roll_menu.edit_text"), "", lambda: self.edit_text(at))
        if sh["kind"] == "custom" and "notes" not in sh and len(app.sels) == 1:
            k = None if sh.get("text") else self.stroke_at(sh, e.x, e.y)
            if k is not None:  # the stroke right-clicked gets picked
                app.set_stroke(k)
                if sh["strokes"][k]["kind"] == "curve":
                    item(tr("roll_menu.add_anchor_here"), "", lambda: self.stroke_click(sh, at))
                    symmetry_menu(m, sh["strokes"][k].get("sym"), lambda mode: self.stroke_symmetry(sh, mode, at))
                item(tr("roll_menu.delete_this_stroke"), tr("roll_menu.del"), lambda: self.delete_stroke(sh, k),
                     keys=True)
                item(tr("roll_menu.copy_this_stroke"), tr("roll_menu.ctrl_c"), lambda: self.copy_stroke(sh, k),
                     keys=True)
            if app.stroke_clip is not None:
                item(tr("roll_menu.paste_stroke_into_this_shape"), tr("roll_menu.ctrl_v"), self.paste_stroke,
                     keys=app.clip_kind == "stroke")
            if k is not None:
                sub = tk.Menu(m, tearoff=0)
                for label, key, fn in (
                        (tr("roll_menu.flip_sideways"), tr("roll_menu.ctrl_h"), lambda: self.flip_stroke(sh, k, True)),
                        (tr("roll_menu.flip_upside_down"), tr("roll_menu.ctrl_j"),
                         lambda: self.flip_stroke(sh, k, False)),
                        (tr("roll_menu.turn_90_left"), tr("roll_menu.ctrl_left"),
                         lambda: self.turn_stroke(sh, k, False)),
                        (tr("roll_menu.turn_90_right"), tr("roll_menu.ctrl_right"),
                         lambda: self.turn_stroke(sh, k, True))):
                    sub.add_command(label=label, accelerator=key, command=fn)
                m.add_cascade(label=tr("roll_menu.this_stroke"), menu=sub)
            item(tr("roll_menu.save_drawing_to_the_shape_library"), "", lambda: app.save_to_library(sh))
        if app.tumour_targets():
            item(tr("roll_menu.tumours"), "", app.open_tumours)
        item(tr("roll_menu.claw_machine"), tr("roll_menu.alt_w"), lambda: open_claw(app), keys=True)
        item(tr("roll_menu.strum"), tr("roll_menu.alt_s"), lambda: open_strum(app), keys=True)
        if len(app.sels) >= 2:  # (greyed out, saying why, when something else is selected too)
            ok = app.can_join()
            item(tr("roll_menu.join_shapes_into_one_curve") if ok else tr("roll_menu.join_shapes_into_one_curve_only"),
                 tr("roll_menu.ctrl_g"), app.join_selected, ok, keys=True)
        if len(app.sels) == 1 and sh["kind"] in LINE_KINDS:
            item(tr("roll_menu.split_here"), "", lambda: app.split_here(i, at))
        if app.live_problem() is None:
            item(tr("roll_menu.turn_into_live_shape"), tr("roll_menu.ctrl_l"), app.turn_into_live, keys=True)
        if len(app.sels) == 1 and app.can_split_pieces(sh):
            label = (tr("roll_menu.split_back_into_the_shapes_it") if originals(sh) else
                     tr("roll_menu.split_into_separate_shapes"))
            item(label, tr("roll_menu.ctrl_shift_g"), lambda: app.split_pieces(i), keys=True)
        # (with a stroke picked, the keys work on it)
        self.group_items(m, item, keys, curve_keys, app.picked() is None)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def show_box_menu(self, e):
        """Right-click inside the kept Select boxes with several shapes selected: only what works on all of them
        at once (user asked; each shape's own options are left out)."""
        app = self.app
        m = tk.Menu(self, tearoff=0)

        def item(label, key, fn, on=True, keys=True):
            m.add_command(label=label, accelerator=key if keys else "", command=fn,
                          state="normal" if on else "disabled")

        m.add_command(label=tr("roll_menu.box_selected", n=len(app.sels)), state="disabled")
        m.add_separator()
        if app.tumour_targets():
            item(tr("roll_menu.tumours"), "", app.open_tumours)
        item(tr("roll_menu.claw_machine"), tr("roll_menu.alt_w"), lambda: open_claw(app))
        item(tr("roll_menu.strum"), tr("roll_menu.alt_s"), lambda: open_strum(app))
        ok = app.can_join()
        item(tr("roll_menu.join_shapes_into_one_curve") if ok else tr("roll_menu.join_shapes_into_one_curve_only"),
             tr("roll_menu.ctrl_g"), app.join_selected, ok)
        if app.live_problem() is None:
            item(tr("roll_menu.turn_into_live_shape"), tr("roll_menu.ctrl_l"), app.turn_into_live)
        self.group_items(m, item, True, True, True)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def group_items(self, m, item, keys, curve_keys, whole):
        """The menu's part for everything selected: delete, duplicate, copy, paste, flip, turn."""
        app = self.app
        n = len(app.sels)
        shapes = tr("roll_menu.shape") if n == 1 else tr("roll_menu.n_shapes", n=n)
        item(tr("roll_menu.delete", shapes=shapes), tr("roll_menu.del"), app.delete_selected, keys=keys and whole)
        item(tr("roll_menu.duplicate", shapes=shapes), tr("roll_menu.ctrl_d"), app.duplicate, keys=True)
        item(tr("roll_menu.copy", shapes=shapes), tr("roll_menu.ctrl_c"), lambda: app.copy_selected(whole=True),
             keys=curve_keys and whole)
        item(tr("roll_menu.paste_at_the_play_line"), tr("roll_menu.ctrl_v"), lambda: app.paste(whole=True),
             bool(app.clipboard),
             keys=curve_keys and app.clip_kind != "stroke")
        m.add_separator()
        item(tr("roll_menu.flip_sideways"), tr("roll_menu.ctrl_h"), lambda: app.flip(True, whole=True),
             keys=curve_keys and whole)
        item(tr("roll_menu.flip_upside_down"), tr("roll_menu.ctrl_j"), lambda: app.flip(False, whole=True),
             keys=curve_keys and whole)
        item(tr("roll_menu.turn_90_left"), tr("roll_menu.ctrl_left"), lambda: app.rotate(False, whole=True), keys=whole)
        item(tr("roll_menu.turn_90_right"), tr("roll_menu.ctrl_right"), lambda: app.rotate(True, whole=True),
             keys=whole)

    def formula_menu(self, m, host=None):
        """"Formula ▸" for the selected curves (host: formula_host.py, default the piano roll's curves). The dots
        show the curve right-clicked's."""
        self._formula_picks = {}  # (kept, so the dots show)
        formula_menu(m, host or RollHost(self.app), self._formula_picks)

    def link_menu(self, m, sh, curves, part):
        """"Link curves ▸" for the highlighted curves. The curve right-clicked keeps its shape, the others follow."""
        source = part[1:] if part and part[0] == "curve" and part[1:] in curves else min(curves)
        others = sorted(curves - {source})
        sub = tk.Menu(m, tearoff=0)
        if others:
            look = self.link_relation(sh, source, others[0])
            closest = tr("roll_menu.flipped") if self.closest_link(sh, source, others[0]) else look
            sub.add_command(label=tr("roll_menu.auto_closest_now", closest=closest),
                            command=lambda: self.link_curves("auto", source))
            sub.add_command(label=look, command=lambda: self.link_curves("same", source))
            sub.add_command(label=tr("roll_menu.flipped_turned_end_to_end"),
                            command=lambda: self.link_curves("flip", source))
            sub.add_separator()
        linked = any(sh["starts"][k]["ends"][end].get("link") is not None for k, end in curves)
        sub.add_command(label=tr("roll_menu.unlink"), command=lambda: self.link_curves(None, source),
                        state="normal" if linked else "disabled")
        m.add_cascade(label=tr("roll_menu.link_curves"), menu=sub)
