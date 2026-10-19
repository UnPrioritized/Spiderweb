"""Piano roll: the right-click menu for shapes (and a funnel's highlighted lines / curves)."""

import copy
import tkinter as tk
from types import SimpleNamespace

import numpy as np

from files.lang import tr
from window.formula_host import FunnelHost, PolygonHost, RollHost, StrokeHost, formula_menu
from notes.areas import most_colours
from notes.convert import originals
from notes.custom import ROLES, can_fit_upright, fit_upright
from window.drawer_mouse import colour_menu
from notes.joined import is_joined, piece_velocity
from notes.text import split_letters, to_custom
from window.join_split import part_glue, span
from notes.mask import is_mask
from notes.merge import merge_parts
from notes.funnel import inside_out, new_start, turned_curve
from notes.tumour import LINE_KINDS
from roll.roll_shared import SHIFT
from window.claw_window import open_claw
from window.path_window import open_path
from window.strum_window import open_strum
from window.chop_window import open_chop, quick_chop
from notes.sustain import is_sustained
from window.sustain_window import any_sustained, carry_stops, open_sustain, sustain_targets, target_indices
from window.widgets import symmetry_menu


class ShapeMenu:
    """Mixed into PianoRoll."""

    def show_menu(self, e, i):
        """Right-click (no drag) near shape i: it gets selected (unless it already is), then the menu."""
        app = self.app
        app.commit_typing()  # (a number typed in a panel box goes to the shapes picked until now, before any item)
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
            what = tr("roll_menu.lines_and_curves" if lines and curves else "roll_menu.lines" if lines
                      else "roll_menu.curves")
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
            hit = self.on_funnel_line(sh, at)
            item(tr("roll_menu.add_curve_start_here") if hit else tr("roll_menu.add_anchor_here"), "",
                 lambda: self.funnel_click(sh, at),
                 new_start(sh, hit[1], hit[0]) is not None if hit else bool(sh["starts"]))  # (greyed: no room)
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
            self.text_items(item)
        if "picture" in sh and len(app.sels) == 1:  # a placed picture (picture.py, image_window.py)
            item(tr("image.menu_look"), tr("image.menu_double"), lambda: app.edit_picture(i), keys=True)
            item(tr("image.menu_other"), "", lambda: app.replace_picture(i))
            m.add_separator()
            item(tr("image.menu_unturn"), "", lambda: app.unturn_picture(i))
            item(tr("image.menu_own_shape"), "", lambda: app.picture_own_shape(i))
        if sh["kind"] == "custom" and "notes" not in sh and len(app.sels) == 1:
            k = None if sh.get("text") else self.stroke_at(sh, e.x, e.y)
            if k is not None:  # the stroke right-clicked gets picked
                app.set_stroke(k)
                if sh["strokes"][k]["kind"] == "curve":
                    item(tr("roll_menu.add_anchor_here"), "", lambda: self.stroke_click(sh, at))
                    symmetry_menu(m, sh["strokes"][k].get("sym"), lambda mode: self.stroke_symmetry(sh, mode, at))
                if StrokeHost(app).targets():  # (a line, polyline, arc or curve; not a polygon's: it has its own)
                    self.formula_menu(m, StrokeHost(app))
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
                sub.add_separator()
                self._role_var = tk.StringVar(value=sh["strokes"][k].get("role") or "both")  # (kept: the dot shows)
                for role in ("both",) + ROLES:
                    sub.add_radiobutton(label=tr("drawer.role_" + role), value=role, variable=self._role_var,
                                        command=lambda r=role: self.set_stroke_role(sh, k, r))
                self._colour_var = tk.IntVar(value=sh["strokes"][k].get("colour", 0))
                sub.add_cascade(label=tr("drawer.outline_colour"), menu=colour_menu(
                    sub, self._colour_var, lambda c: self.set_stroke_colour(sh, k, c),
                    most_colours(self.app.all16())),
                    state="disabled" if sh["strokes"][k].get("role") == "cut" else "normal")
                m.add_cascade(label=tr("roll_menu.this_stroke"), menu=sub)
            item(tr("roll_menu.save_drawing_to_the_shape_library"), "", lambda: app.save_to_library(sh))
            if not sh.get("text"):  # (greyed while the box is straight: it already hugs the drawing)
                item(tr("roll_menu.fit_box_to_drawing"), "", lambda: self.fit_box(i), can_fit_upright(sh))
        if app.note_tool_sels():  # (right under Save drawing, user)
            item(tr("roll_menu.copy_path"), "", lambda: open_path(app))
        if app.tumour_targets():
            item(tr("roll_menu.tumours"), "", app.open_tumours)
        if app.note_tool_sels():  # (pictures take no note tools, user)
            item(tr("roll_menu.claw_machine"), tr("roll_menu.alt_w"), lambda: open_claw(app), keys=True)
            item(tr("roll_menu.strum"), tr("roll_menu.alt_s"), lambda: open_strum(app), keys=True)
            item(tr("roll_menu.chop"), tr("roll_menu.alt_u"), lambda: open_chop(app), keys=True)
            item(tr("roll_menu.quick_chop"), tr("roll_menu.ctrl_u"), lambda: quick_chop(app), keys=True)
            self.glue_items(item)
        self.sustain_item(m)
        if is_sustained(sh) and i in target_indices(app):  # (where on the shape: roll_sustain.py)
            f = self.stop_here(i, at)
            item(tr("sustain.add_here"), "", lambda: self.add_stop_here(i, f), f is not None)
        self.between_items(item, i)
        self.merge_menu(m)
        self.mask_items(item)
        if len(app.sels) >= 2:  # (greyed out, saying why, when something else is selected too)
            ok = app.can_join()
            item(tr("roll_menu.join_shapes_into_one_curve") if ok else tr("roll_menu.join_shapes_into_one_curve_only"),
                 tr("roll_menu.ctrl_g"), app.join_selected, ok, keys=True)
        if len(app.sels) == 1 and sh["kind"] in LINE_KINDS:
            item(tr("roll_menu.split_here"), "", lambda: app.split_here(i, at))
        if app.pieces():  # (cut by Split here / Slice: its notes follow the shape it was cut from, sliced.py)
            item(tr("roll_menu.make_complete"), "", app.make_complete)
        if app.live_problem() is None:
            item(tr("roll_menu.turn_into_live_shape"), tr("roll_menu.ctrl_r"), app.turn_into_live, keys=True)
        if app.plain_targets():
            item(tr("roll_menu.turn_into_plain_notes"), "", app.turn_into_plain_notes)
        if len(app.sels) == 1 and app.can_split_pieces(sh):
            label = (tr("roll_menu.split_back_into_the_shapes_it") if originals(sh) or merge_parts(sh) else
                     tr("roll_menu.split_into_separate_shapes"))
            item(label, tr("roll_menu.ctrl_shift_g"), lambda: app.split_pieces(i), keys=True)
        # (with a stroke picked, the keys work on it)
        self.group_items(m, item, keys, curve_keys, app.picked() is None)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def fit_box(self, i):
        """A turned / skewed custom shape's box straight again round its drawing (custom.fit_upright). One step. The
        notes are checked: anything different = nothing done and a message (user: it must keep what's shown)."""
        app = self.app
        sh = app.shapes[i]
        was, before = copy.deepcopy(sh), app.notes_of(sh)
        if not fit_upright(sh) or not np.array_equal(app.notes_of(sh), before):
            sh.clear()
            sh.update(was)
            app.status.config(text=tr("roll_menu.fit_box_cant"))
            return
        fitted = copy.deepcopy(sh)
        sh.clear()
        sh.update(was)
        app.push_undo(name=tr("roll_menu.fit_box_to_drawing"))
        sh.clear()
        sh.update(fitted)
        self.box_kept = None  # (a kept Select box was round the old box)
        app.shapes_changed()

    def text_items(self, item):
        """Split into letters (not Hz bass: its tones belong to the whole text) and Turn into custom shape, for the
        selected texts."""
        shapes = [self.app.shapes[j] for j in self.app.sels]
        whole = not any(sh.get("cut") for sh in shapes)  # (a sliced piece's notes come from the whole text)
        item(tr("roll_menu.split_into_letters"), "", self.split_letters, whole and not any(sh.get("hz") for sh in shapes)
             and any(sum(not c.isspace() for c in sh["text"]["text"]) > 1 for sh in shapes))
        item(tr("roll_menu.text_to_custom"), "", self.text_to_custom, whole)

    def split_letters(self):
        """Each selected text becomes one-letter texts where its letters were (text.split_letters), all selected,
        velocities kept where they were. One step."""
        app = self.app
        self.end_typing()
        self.cancel_draft()
        app.push_undo(name=tr("roll_menu.split_into_letters"))
        picked = []  # (indices in the new list)
        changed = False
        rows = lambda ns: sorted(map(tuple, ns[:, :4].tolist()))
        for j in sorted(app.sels, reverse=True):  # (from the back: the shapes before it keep their numbers)
            sh = app.shapes[j]
            letters = split_letters(sh, app.notes_of, app.ppq)
            if len(letters) < 2:
                picked.append(j)
                continue
            whole = span(sh)
            for p in letters:
                p.pop("between", None)
                piece_velocity(p, sh, span(p), whole)
                part_glue(p, sh)
            carry_stops(app, [sh], letters)  # (Sustain stops put by hand stay on their ticks)
            changed = changed or rows(app.notes_of(sh)) != rows(np.concatenate([app.notes_of(p) for p in letters]))
            app.unlink_groups([j])
            app.shapes[j:j + 1] = letters
            picked = [d + len(letters) - 1 for d in picked] + list(range(j, j + len(letters)))
        self.box_kept = None
        app.select_many(picked, min(picked))
        app.shapes_changed()
        if changed:  # (user: split anyway, but say so)
            app.status.config(text=tr("roll_menu.split_notes_changed"))

    def text_to_custom(self):
        """The selected texts become plain custom shapes (text.to_custom). One step."""
        app = self.app
        self.end_typing()
        self.cancel_draft()
        app.push_undo(name=tr("roll_menu.text_to_custom"))
        for j in app.sels:
            to_custom(app.shapes[j])
        app.select_many(app.sels, app.sel)  # (the panel shows custom shape settings now)
        app.shapes_changed()

    def show_box_menu(self, e, i=None):
        """Right-click inside the kept Select boxes with several shapes selected: only what works on all of them
        at once (user asked; each shape's own options are left out). i: the shape right-clicked on, if any."""
        app = self.app
        app.commit_typing()  # (a number typed in a panel box: to the selected shapes first)
        m = tk.Menu(self, tearoff=0)

        def item(label, key, fn, on=True, keys=True):
            m.add_command(label=label, accelerator=key if keys else "", command=fn,
                          state="normal" if on else "disabled")

        m.add_command(label=tr("roll_menu.box_selected", n=len(app.sels)), state="disabled")
        m.add_separator()
        n = len(app.sels)  # (user, 2026-10-02: shapes that all take a formula get Formula ▸, changing them all)
        if len(app.pattern_targets()) == n:
            self.formula_menu(m)
        elif len(app.polygon_shapes()) == n:
            self.formula_menu(m, PolygonHost(app))
        if all(app.shapes[j].get("text") for j in app.sels):
            self.text_items(item)
        if app.note_tool_sels():  # (where the one shape's menu has it: under Save drawing, user)
            item(tr("roll_menu.copy_path"), "", lambda: open_path(app))
        if app.tumour_targets():
            item(tr("roll_menu.tumours"), "", app.open_tumours)
        if app.note_tool_sels():  # (pictures take no note tools, user)
            item(tr("roll_menu.claw_machine"), tr("roll_menu.alt_w"), lambda: open_claw(app))
            item(tr("roll_menu.strum"), tr("roll_menu.alt_s"), lambda: open_strum(app))
            item(tr("roll_menu.chop"), tr("roll_menu.alt_u"), lambda: open_chop(app))
            item(tr("roll_menu.quick_chop"), tr("roll_menu.ctrl_u"), lambda: quick_chop(app))
            self.glue_items(item)
        self.sustain_item(m)
        if app.between_pair():
            item(tr("between.menu_add"), "", app.add_between)
        self.merge_menu(m)
        self.mask_items(item)
        ok = app.can_join()
        item(tr("roll_menu.join_shapes_into_one_curve") if ok else tr("roll_menu.join_shapes_into_one_curve_only"),
             tr("roll_menu.ctrl_g"), app.join_selected, ok)
        if app.live_problem() is None:
            item(tr("roll_menu.turn_into_live_shape"), tr("roll_menu.ctrl_r"), app.turn_into_live)
        if app.plain_targets():
            item(tr("roll_menu.turn_into_plain_notes"), "", app.turn_into_plain_notes)
        if app.pieces():
            item(tr("roll_menu.make_complete"), "", app.make_complete)
        self.group_items(m, item, True, True, True)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def between_items(self, item, i):
        """Add between (between.py): two open lines selected = start one; a group's shape = its own items."""
        app = self.app
        if app.between_pair():
            item(tr("between.menu_add"), "", app.add_between)
        b = app.shapes[i].get("between")
        if not b:
            return
        item(tr("between.menu_edit"), tr("between.menu_double"), lambda: app.edit_between(i), keys=True)
        item(tr("between.menu_select"), "", lambda: app.select_group(i))
        if b["role"] == "key":
            item(tr("between.menu_unkey"), "", lambda: app.unkey(i))
        if b.get("vel"):
            item(tr("between.menu_group_vel"), "", lambda: app.group_velocity(i))
        item(tr("between.menu_unlink"), "", lambda: app.unlink_between(i))

    def mask_items(self, item):
        """Apply mask, Combine masks / Separate masks, when masks are selected."""
        app = self.app
        if not any(is_mask(app.shapes[j]) for j in app.sels if j < len(app.shapes)):
            return
        item(tr("mask.apply"), "", app.apply_masks)
        if app.can_combine():
            item(tr("mask.combine"), "", app.combine_masks)
        if app.can_separate():
            item(tr("mask.separate"), "", app.separate_masks)

    def merge_menu(self, m):
        """Gate sensitive merge ▸ Merge to right / Merge to left (two shapes selected; no anchor: where the menu was
        opened doesn't matter, user)."""
        app = self.app
        if not app.merge_pair():
            return
        sub = tk.Menu(m, tearoff=0)
        for right in (True, False):
            sub.add_command(label=tr("join_split.merge_right" if right else "join_split.merge_left"),
                            command=lambda right=right: app.gate_merge(right))
        sub.add_separator()  # (the way to merge, user 2026-10-19: one shape holding both, or the other left as it is)
        sub.add_checkbutton(label=tr("join_split.merge_apart"), variable=app.merge_apart)
        m.add_cascade(label=tr("join_split.merge_menu"), menu=sub)

    def sustain_item(self, m):
        """Sustain… (sustain_window.py), ticked while a selected shape is sustained; greyed when every selected shape
        is a Hz bass; left out when every one is an image (like the note tools, user)."""
        app = self.app
        if not app.note_tool_sels():
            return
        self._sustain_var = tk.BooleanVar(value=any_sustained(app))  # (kept: the tick shows)
        m.add_checkbutton(label=tr("sustain.menu"), variable=self._sustain_var, command=lambda: open_sustain(app),
                          state="normal" if sustain_targets(app) else "disabled")

    def glue_items(self, item):
        """Glue (glue.py): in the kept Select boxes if there are any, else all the selected shapes' notes."""
        app = self.app
        item(tr("roll_menu.glue_in_box") if self.kept_box() else tr("roll_menu.glue"), "", app.glue_selected)
        if any(app.shapes[i].get("glue") for i in app.sels):
            item(tr("roll_menu.remove_glue"), "", app.unglue_selected)

    def group_items(self, m, item, keys, curve_keys, whole):
        """The menu's part for everything selected: delete, duplicate, copy, paste, flip, turn."""
        app = self.app
        n = len(app.sels)
        shapes = tr("roll_menu.shape") if n == 1 else tr("roll_menu.n_shapes", n=n)
        item(tr("roll_menu.delete", shapes=shapes), tr("roll_menu.del"), app.delete_selected, keys=keys and whole)
        item(tr("roll_menu.duplicate", shapes=shapes), tr("roll_menu.ctrl_d"), app.duplicate, keys=True)
        item(tr("roll_menu.cut", shapes=shapes), tr("roll_menu.ctrl_x"), lambda: app.cut_selected(whole=True),
             keys=curve_keys and whole)
        item(tr("roll_menu.copy", shapes=shapes), tr("roll_menu.ctrl_c"), lambda: app.copy_selected(whole=True),
             keys=curve_keys and whole)
        item(tr("roll_menu.paste_at_the_play_line"), tr("roll_menu.ctrl_v"), lambda: app.paste(whole=True),
             bool(app.clipboard) or app.shared_clip() is not None or app.domino_newer(),
             keys=curve_keys and app.clip_kind != "stroke")
        m.add_separator()
        gf = app.gate_flip_state()
        if gf:  # (above the normal flips, ticked while on; greyed saying why in a few words, user)
            self._gate_flip_var = tk.BooleanVar(value=gf[1])  # (kept: the tick shows)
            m.add_checkbutton(label=tr("roll_menu.gate_flip_" + gf[2]) if gf[2] else tr("roll_menu.gate_flip"),
                              variable=self._gate_flip_var, command=app.gate_flip,
                              state="normal" if gf[0] else "disabled")
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
