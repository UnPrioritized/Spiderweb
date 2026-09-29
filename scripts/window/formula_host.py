"""Where curve formulas (pattern.py) can be put: the piano roll's curves, the drawer's curve strokes and a funnel's
curves. Each kind of place is a "host": the right-click menu's Formula items and the Custom… window
(pattern_dialog.py) work through it, so they look and work the same everywhere.

A host's "holders" are the dicts that get "shape" / "pattern" (a curve shape, a drawer stroke, a funnel curve).
Their formulas' k (how many x one y is where they look round) and a pattern's scale (keys -> the holder's units) are
worked out as it looks when they're put on, so they look the same wherever they are. A pattern's sideways sizes: keys
on the piano roll (a funnel's too), a share of the board in the drawer (100 = the whole board)."""

import copy
import json
import math
import tkinter as tk

from files.lang import tr
from notes.bezier import anchor_count
from notes.pattern import (PATTERN_PRESETS, SHAPE_NAMES, SHAPE_PRESETS, baked_path, has_formula, loop_length,
                           new_pattern, new_shape, pattern_name, shape_name)

STRAIGHT = [[0.0, 0.0], [1 / 3, 1 / 3], [2 / 3, 2 / 3], [1.0, 1.0]]  # a funnel curve's origin when given a shape
DRAWER_SCALE = 0.01  # a drawer pattern's sizes: shares of the board, in 100ths


def layer_name(layer, p):
    return shape_name(p) if layer == "shape" else pattern_name(p)


def step_name(layer, name):
    return tr("panel_pattern.shape_step" if layer == "shape" else "panel_pattern.pattern_step", name=name)


SYM_CHOICES = (("", "widgets.off"), ("mirror", "formula_host.mirrored"),
               ("turn", "formula_host.turned"))  # a formula's symmetric halves (pattern.py)


def sym_label(mode):
    return tr(dict(SYM_CHOICES)[mode or ""])


def set_loop_sym(loop, mode):
    """A loop edited by hand (anchors + handles) with symmetric halves on / off; its first half stays. A closed
    shape is mirrored across the line through its start ("flip", bezier.py)."""
    from notes.bezier import set_symmetry
    if mode == "mirror" and math.dist(loop["pts"][0], loop["pts"][-1]) < 1e-9:
        mode = "flip"
    set_symmetry(loop, mode, 0, lambda p: (p[0], p[1]))


def sym_menu(m, host, layer, picks):
    """"Symmetric halves ▸" for the holders' shape / pattern (greyed when the first has none)."""
    p = (host.current() or {}).get(layer)
    sub = tk.Menu(m, tearoff=0)
    pick = picks[layer + "_sym"] = tk.StringVar(m, value=(p or {}).get("sym", ""))
    for value, key in SYM_CHOICES:
        sub.add_radiobutton(label=tr(key), value=value, variable=pick,
                            command=lambda v=value: host.set_sym(layer, v or None))
    m.add_cascade(label=tr("widgets.symmetric_halves"), menu=sub, state="normal" if p else "disabled")


class FormulaHost:
    """What every host shares; the others fill in where they differ."""
    parent = None  # the window the Custom… window belongs to
    app = None
    pattern_help = "pattern_dialog.help"  # (texts that say what a pattern's sizes are in)
    number_tip = "panel_pattern.number_tip"
    sym_modes = ("mirror", "turn")  # symmetric halves a baked curve can get (a funnel's curves have none)

    def targets(self):
        """The holders the menu / window change."""
        return []

    def fresh(self, holder, layer):
        """{"k", maybe "scale"} for a formula newly put on holder."""
        return {"k": 1.0, "scale": 1.0}

    def placed(self, holder, layer, before):
        """A formula was just put on holder (before: how it was)."""

    def spread(self):
        """After a change: whatever has to follow the holders (a funnel's linked curves)."""

    def begin(self, name):
        """An undo step, before a change."""

    def changed(self, final=True):
        """Show the change (final: done, not a live preview)."""

    def snapshot(self):
        """How it is now, for restore / commit."""
        return [(h, copy.deepcopy(h)) for h in self.targets()]

    def restore(self, snap):
        for h, old in snap:
            h.clear()
            h.update(copy.deepcopy(old))
        self.changed()

    def commit(self, snap, name):
        """The changes since snap as one undo step."""

    def loop_length(self, holder, pat):
        """One loop of the pattern on holder, in the pattern's own sizes (keys), 0 if it can't be worked out."""
        try:
            return loop_length(dict(holder, pattern=pat)) / pat.get("scale", 1.0)
        except (ValueError, ZeroDivisionError, KeyError, IndexError):
            return 0.0

    def bake(self, holder):
        """Turn into plain curve: the formulas become ordinary anchors and handles."""
        pts, sharp, sym = baked_path(holder, holder["pts"], self.sym_modes)
        for key in ("shape", "pattern", "sym", "rev"):
            holder.pop(key, None)
        holder["pts"] = pts
        holder["sharp"] = sharp
        if sym:
            holder["sym"] = sym

    # ------------------------------------------------------------ what the menu does
    def current(self):
        tgts = self.targets()
        return tgts[0] if tgts else None

    def new_formula(self, holder, layer, preset, saved):
        from window.pattern_dialog import saved_pattern, saved_shape
        got = self.fresh(holder, layer)
        if layer == "shape":
            return saved_shape(saved, got["k"]) if saved else new_shape(preset, got["k"], holder.get("shape"))
        old = holder.get("pattern")
        new = saved_pattern(saved, got["k"], old) if saved else new_pattern(preset, got["k"], old)
        new["scale"] = got.get("scale", 1.0)
        return new

    def set_formula(self, layer, preset, saved=None):
        """A preset (None: none) or a saved one (pattern_dialog.load_patterns) on every holder."""
        tgts = self.targets()
        if not tgts:
            return
        if saved:
            name = saved["name"]
        elif preset:
            name = SHAPE_NAMES[preset] if layer == "shape" else pattern_name({"preset": preset})
        else:
            name = None
        self.begin(step_name(layer, name) if name else tr("panel_pattern.remove_" + layer))
        for h in tgts:
            if preset is None and not saved:
                h.pop(layer, None)
                if not has_formula(h):
                    h.pop("rev", None)
            else:
                before = copy.deepcopy(h)
                h[layer] = self.new_formula(h, layer, preset, saved)
                self.placed(h, layer, before)
        self.spread()
        self.changed()

    def set_sym(self, layer, mode):
        """Symmetric halves of every holder's shape / pattern: "mirror", "turn" or None (off). One edited by hand
        gets them too (its first half stays)."""
        tgts = [h for h in self.targets() if h.get(layer) and h[layer].get("sym") != mode]
        if not tgts:
            return
        self.begin(tr("pattern_dialog.symmetric_step", name=sym_label(mode)))
        for h in tgts:
            p = h[layer]
            if mode:
                p["sym"] = mode
            else:
                p.pop("sym", None)
            if p.get("loop"):
                set_loop_sym(p["loop"], mode)
        self.spread()
        self.changed()

    def with_formula(self):
        return [h for h in self.targets() if has_formula(h)]

    def remove(self):
        tgts = self.with_formula()
        if not tgts:
            return
        self.begin(tr("panel_pattern.remove_formula"))
        for h in tgts:
            for key in ("shape", "pattern", "rev"):
                h.pop(key, None)
        self.spread()
        self.changed()

    def plain(self):
        tgts = self.with_formula()
        if not tgts:
            return
        self.begin(tr("panel_pattern.turn_into_plain_curve"))
        for h in tgts:
            self.bake(h)
        self.spread()
        self.changed()

    def open_dialog(self, layer):
        from window.pattern_dialog import FormulaDialog
        if self.targets():
            FormulaDialog(self, layer)


def formula_menu(m, host, picks):
    """"Formula ▸" for host's holders: the shape of the curve and the pattern along it, taking them off, or making
    them plain anchors and handles. The dots show the first holder's. picks: a dict that keeps the dots' variables
    (they must live as long as the menu)."""
    from window.pattern_dialog import load_patterns
    h = host.current() or {}
    sub = tk.Menu(m, tearoff=0)
    for layer, label, presets in (
            ("shape", tr("roll_menu.shape_of_the_line"), [(sid, name) for sid, name, _, _, _ in SHAPE_PRESETS]),
            ("pattern", tr("roll_menu.pattern_along_the_line"),
             [(pid, name) for pid, name, _, _ in PATTERN_PRESETS])):
        pat = h.get(layer) or {}
        if not pat:
            now = "none"
        elif pat.get("name"):  # a saved one
            now = "saved:" + pat["name"]
        else:  # a preset (edited by hand: none of the list any more)
            now = "" if pat.get("loop") else pat.get("preset", "")
        pick = picks[layer] = tk.StringVar(m, value=now)
        menu = tk.Menu(sub, tearoff=0)
        menu.add_command(label=tr("roll_menu.custom"), command=lambda l=layer: host.open_dialog(l))
        menu.add_separator()
        menu.add_radiobutton(label=tr("roll_menu.none"), value="none", variable=pick,
                             command=lambda l=layer: host.set_formula(l, None))
        for pid, name in presets:
            menu.add_radiobutton(label=name, value=pid, variable=pick,
                                 command=lambda l=layer, p=pid: host.set_formula(l, p))
        saved = load_patterns(layer)
        if saved:
            menu.add_separator()
        for item in saved:
            menu.add_radiobutton(label=item["name"], value="saved:" + item["name"], variable=pick,
                                 command=lambda l=layer, it=item: host.set_formula(l, None, it))
        menu.add_separator()
        sym_menu(menu, host, layer, picks)
        sub.add_cascade(label=label, menu=menu)
    sub.add_separator()
    on = "normal" if host.with_formula() else "disabled"
    sub.add_command(label=tr("roll_menu.remove_formula"), command=host.remove, state=on)
    sub.add_command(label=tr("roll_menu.turn_into_plain_curve"), command=host.plain, state=on)
    m.add_cascade(label=tr("roll_menu.formula"), menu=sub)


# ---------------------------------------------------------------- the piano roll's curve shapes

class RollHost(FormulaHost):
    def __init__(self, app):
        self.app = self.parent = app

    def targets(self):
        return self.app.pattern_targets()

    def fresh(self, holder, layer):
        roll = self.app.roll
        return {"k": roll.sy / roll.sx if roll.sx else 0.25, "scale": 1.0}  # as the piano roll looks now

    def begin(self, name):
        self.app.push_undo(name=name)

    def changed(self, final=True):
        self.app.shapes_changed()
        if final:
            self.app.sync_panel()
        else:
            self.app.sync_pattern()

    def snapshot(self):
        return json.dumps(self.app.shapes), super().snapshot()

    def restore(self, snap):
        super().restore(snap[1])

    def commit(self, snap, name):
        self.app.push_undo(snap[0], name)
        self.app.sync_panel()

    def bake(self, sh):
        from notes.joined import shown_tumour
        from notes.pattern import baked
        got = baked(sh)
        tm = shown_tumour(sh)
        for key in ("shape", "pattern", "sym", "tumours", "splits", "gaps", "sharp", "k"):
            sh.pop(key, None)
        sh["kind"] = "curve"  # (a line / an arc becomes a curve)
        sh["pts"] = got["pts"]
        if got["sharp"]:
            sh["sharp"] = got["sharp"]
        if got["gaps"]:
            sh["gaps"] = got["gaps"]
        if got["sym"]:
            sh["sym"] = got["sym"]
        if tm:
            sh["tumour"] = tm



# ---------------------------------------------------------------- the drawer's curve strokes

class DrawerHost(FormulaHost):
    pattern_help = "pattern_dialog.help_drawer"
    number_tip = "panel_pattern.number_tip_drawer"

    def __init__(self, drawer):
        self.drawer = self.parent = drawer
        self.app = drawer.app

    def targets(self):
        d = self.drawer
        return [d.strokes[d.sel]] if d.sel is not None and d.strokes[d.sel]["kind"] == "curve" else []

    def fresh(self, holder, layer):
        return {"k": 1.0, "scale": DRAWER_SCALE}  # (the board is square on screen)

    def begin(self, name):
        self.drawer.push_undo()

    def changed(self, final=True):
        self.drawer.changed()

    def snapshot(self):
        return json.dumps(self.drawer.strokes), super().snapshot()

    def restore(self, snap):
        super().restore(snap[1])

    def commit(self, snap, name):
        self.drawer.push_undo(snap[0])
        self.drawer.changed()


# ---------------------------------------------------------------- a funnel's curves (in their boxes: funnel.py)

class FunnelHost(FormulaHost):
    """The highlighted curves of the selected funnel. Shapes are laid out in the curve's box as it is (so Slow start
    is x² from the start to the wall end, like the old curve formulas); patterns as the piano roll looks. Linked
    curves get the same formulas (turned end to end: they run from the other end)."""
    sym_modes = ()

    def __init__(self, roll):
        self.roll = roll
        self.app = self.parent = roll.app

    def found(self):
        got = self.roll.funnel_parts()
        return (got[0], sorted(got[2])) if got and got[2] else (None, [])

    def targets(self):
        sh, curves = self.found()
        return [sh["starts"][k]["ends"][end] for k, end in curves]

    def box_of(self, holder):
        from notes.funnel import curve_box
        sh, curves = self.found()
        for k, end in curves:
            if sh["starts"][k]["ends"][end] is holder:
                st = sh["starts"][k]
                return curve_box(sh, st["at"], end, st.get("line", 0))
        return None

    def fresh(self, holder, layer):
        roll = self.roll
        box = self.box_of(holder)
        if layer == "shape" or not box or not roll.sx:
            return {"k": 1.0, "scale": 1.0}
        kk = roll.sy / roll.sx
        _, (ub, up), (vb, vp) = box
        lu, lv = math.hypot(ub / kk, up), math.hypot(vb / kk, vp)  # the box's sides on screen, in keys
        if lu < 1e-9 or lv < 1e-9:
            return {"k": 1.0, "scale": 1.0}
        return {"k": lv / lu, "scale": 1 / lv}

    def placed(self, holder, layer, before):
        if not has_formula(before):  # linked curves turned end to end run from their end
            holder["rev"] = bool(holder.get("link") is not None and holder.get("flip"))
        if layer == "shape" and not before.get("shape"):  # the shape is laid along a straight line from A to B
            holder["pts"] = [list(p) for p in STRAIGHT]
            holder["sharp"] = []

    def spread(self):
        """Linked curves that aren't highlighted take the formulas too."""
        from notes.funnel import partners
        sh, curves = self.found()
        if not sh:
            return
        for k, end in curves:
            c = sh["starts"][k]["ends"][end]
            for k2, e2, flip in partners(sh, k, end):
                if (k2, e2) in curves:
                    continue
                c2 = sh["starts"][k2]["ends"][e2]
                for key in ("shape", "pattern"):
                    if c.get(key):
                        c2[key] = copy.deepcopy(c[key])
                    else:
                        c2.pop(key, None)
                if has_formula(c):
                    c2["rev"] = c.get("rev", False) != flip
                else:
                    c2.pop("rev", None)

    def begin(self, name):
        self.app.push_undo(name=name)

    def changed(self, final=True):
        self.app.shape_edited()
        self.app.sync_funnel()

    def snapshot(self):
        sh, _ = self.found()
        curves = [c for st in sh["starts"] for c in st["ends"] if c] if sh else []
        return json.dumps(self.app.shapes), [(c, copy.deepcopy(c)) for c in curves]

    def restore(self, snap):
        super().restore(snap[1])

    def commit(self, snap, name):
        self.app.push_undo(snap[0], name)

    def bake(self, holder):
        super().bake(holder)
        holder["pts"][0], holder["pts"][-1] = [0.0, 0.0], [1.0, 1.0]  # (a funnel curve runs from A to B)
        n = anchor_count(holder["pts"])
        holder["sharp"] = [a for a in holder["sharp"] if 0 < a < n - 1]
