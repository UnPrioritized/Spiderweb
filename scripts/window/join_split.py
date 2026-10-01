"""Join (selected lines / polylines / freehand strokes / curves / arcs -> one curve) and Split (a joined curve back
into pieces, any of those cut in two where it was right-clicked, a custom shape into its separate drawings). The
maths is in notes/joined.py."""

import copy
import math
from tkinter import messagebox, ttk

import numpy as np

from files.lang import tr
from notes.arc import arc_circle, arc_points
from notes.convert import CAN_TURN, losses, originals, to_live
from notes.bezier import anchor_count, nearest, split
from notes.engine import shape_path
from notes.smooth import smooth_path
from notes.joined import (custom_groups, join_shapes, join_velocity, piece_velocity, sections, split_at, split_custom,
                          split_pieces)
from notes.tumour import LINE_KINDS, split_tumour
from roll.roll_shared import cached_path, cached_strokes
from window.widgets import Tooltip

TOUCH_PX = 8  # ends closer than this on screen (times the display scaling) count as touching, like Live shape snaps


def span(sh):
    bs = [b for b, _ in cached_path(sh)]
    return min(bs), max(bs)


JOIN_KINDS = tr("join_split.lines_polylines_freehand_strokes_curves")
LINE_FILL_TIP = tr("join_split.lines_can_t_be_filled_turn")


class JoinSplit:
    """Mixed into App."""

    def _build_line_fill(self):
        """A greyed-out Inside row for lines: says how to fill them."""
        row = self.line_fill_row = ttk.Frame(self.settings)
        ttk.Label(row, text=tr("join_split.inside"), foreground="#999").pack(side="left")
        for text in (tr("join_split.empty"), tr("join_split.fill"), tr("join_split.spam"),
                     tr("join_split.outline_spam")):
            b = ttk.Radiobutton(row, text=text, value=text, state="disabled")
            b.pack(side="left", padx=(5, 0))
            Tooltip(b, LINE_FILL_TIP)
        Tooltip(row, LINE_FILL_TIP)

    def sync_line_fill(self):
        self._rows["line_fill"] = bool(self.sels) and all(
            i < len(self.shapes) and self.shapes[i]["kind"] in LINE_KINDS for i in self.sels)
        self.layout_rows()

    def live_problem(self):
        """Why the selection can't be turned into a live shape (None = it can)."""
        sels = [i for i in self.sels if i < len(self.shapes)]
        if not sels:
            return tr("join_split.select_the_shapes_to_turn_into", JOIN_KINDS=JOIN_KINDS)
        shapes = [self.shapes[i] for i in sels]
        if any(sh["kind"] not in CAN_TURN or sh.get("text") or "notes" in sh for sh in shapes):
            other = sorted({self.shape_label(sh).split(":")[0] for sh in shapes
                            if sh["kind"] not in CAN_TURN or sh.get("text") or "notes" in sh})
            return tr("join_split.only_and_custom_shapes_can_be", JOIN_KINDS=JOIN_KINDS,
                      join=tr("roll_funnel.and").join(other).lower())
        if all(sh["kind"] == "custom" for sh in shapes) and len(shapes) == 1:
            return tr("join_split.it_s_a_custom_shape_already")
        return None

    def turn_into_live(self):
        """The selected shapes -> one live shape (notes/convert.py), where the first of them was."""
        problem = self.live_problem()
        if problem:  # (the shortcut: say why)
            self.status.config(text=problem)
            return
        order = sorted(self.sels)
        olds = [self.shapes[i] for i in order]
        lost = losses(olds)
        if lost and not messagebox.askokcancel(
                tr("join_split.spiderweb"), tr("join_split.turning_these_into_a_live_shape") + "\n• ".join(lost) +
                tr("join_split.split_into_separate_shapes_or_ctrl"), icon="warning", parent=self):
            return
        new = to_live(olds, [cached_strokes(sh) for sh in olds], self.defaults, self.custom_defaults)
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.turn_into_live_shape"))
        at = order[0]
        for i in reversed(order):
            del self.shapes[i]
        self.shapes.insert(at, new)
        self.select(at)
        self.shapes_changed()
        n = len(olds)
        self.status.config(text=tr("join_split.turned_the_shape_into_a_live")
                           if n == 1 else
                           tr("join_split.turned_shapes_into_a_live_shape", n=n))
        self.tips.show("turn_live", wait=True)

    def join_problem(self):
        """Why the selection can't be joined (None = it can)."""
        sels = [i for i in self.sels if i < len(self.shapes)]
        if len(sels) < 2:
            return tr("join_split.select_two_or_more_to_join", JOIN_KINDS=JOIN_KINDS.replace(' and ', ' or '))
        other = sorted({self.shape_label(self.shapes[i]).split(":")[0] for i in sels
                        if self.shapes[i]["kind"] not in LINE_KINDS})
        if other:
            return tr("join_split.only_can_be_joined_selected", JOIN_KINDS=JOIN_KINDS,
                      join=tr("roll_funnel.and").join(other).lower())
        return None

    def split_problem(self):
        """Why the selection can't be split into separate shapes (None = it can)."""
        if len(self.sels) != 1 or self.sel is None or self.sel >= len(self.shapes):
            return tr("join_split.select_one_shape_to_split_it")
        sh = self.selected()
        if sh["kind"] == "custom" and (sh.get("text") or "notes" in sh):
            return tr("join_split.text_and_pasted_notes_can_t")
        if not self.can_split_pieces(sh):
            return (tr("join_split.it_s_all_one_piece_nothing") if sh["kind"] in ("curve", "custom")
                    else tr("join_split.only_joined_curves_and_custom_shapes"))
        return None

    def split_selected(self):
        problem = self.split_problem()
        if problem:  # (the shortcut: say why)
            self.status.config(text=problem)
        else:
            self.split_pieces(self.sel)

    def can_join(self):
        return self.join_problem() is None

    def join_selected(self):
        problem = self.join_problem()
        if problem:  # (the shortcut: say why)
            self.status.config(text=problem)
            return
        if self.roll.sx is None:
            return
        roll = self.roll

        def touch(p, q):
            return math.hypot(roll.t2x(p[0]) - roll.t2x(q[0]), roll.p2y(p[1]) - roll.p2y(q[1])) <= TOUCH_PX * self.scale

        order = sorted(self.sels)
        olds = [self.shapes[i] for i in order]
        new = join_shapes(olds, roll.sy / roll.sx, touch)
        if new is None:
            return
        join_velocity(new, olds, [span(sh) for sh in olds], span(new))  # (each keeps its velocities)
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.join"))
        at = order[0]
        for i in reversed(order):
            del self.shapes[i]
        self.shapes.insert(at, new)
        self.select(at)
        self.shapes_changed()
        pieces = len(new.get("gaps", [])) + 1
        self.status.config(text=tr("join_split.joined_shapes_into_one_curve", n=len(order)) +
                           (tr("join_split.pieces_some_ends_didn_t_touch", pieces=pieces) if pieces > 1 else ""))
        self.tips.show("join", wait=True)

    def can_split_pieces(self, sh):
        """A joined curve with more than one piece / shape in it, a live shape that can go back to the shapes it was
        made of, or a custom drawing with separate parts."""
        if sh["kind"] == "custom" and originals(sh):
            return True
        if sh["kind"] == "curve":
            return len(sections(sh)) > 1
        return (sh["kind"] == "custom" and "notes" not in sh and not sh.get("text") and
                len(custom_groups(sh)) > 1)

    def replace_shape(self, i, parts, velocity=True, name=tr("join_split.split")):
        """Shape i replaced by parts (selected), velocities kept where they were (velocity=False: the parts have
        their own)."""
        old = self.shapes[i]
        self.roll.cancel_draft()
        self.push_undo(name=name)
        whole = span(old)
        for p in parts if velocity else ():
            piece_velocity(p, old, span(p), whole)
        self.shapes[i:i + 1] = parts
        self.select_many(range(i, i + len(parts)), i)
        self.shapes_changed()

    def split_pieces(self, i):
        sh = self.shapes[i]
        if not self.can_split_pieces(sh):
            return
        back = originals(sh) if sh["kind"] == "custom" else None
        if back:  # the shapes it was made of (Turn into live shape), as they were
            self.replace_shape(i, back, velocity=False, name=tr("join_split.split_back_into_the_old_shapes"))
            n = len(back)
            self.status.config(text=tr("join_split.back_to_the_shape_it_was") if n == 1 else
                               tr("join_split.back_to_the_shapes_it_was", n=n))
            self.tips.show("turn_live", wait=True)
            return
        parts = split_pieces(sh) if sh["kind"] == "curve" else split_custom(sh)
        self.replace_shape(i, parts)
        self.status.config(text=tr("join_split.split_into_shapes", n=len(parts)))
        self.tips.show("join", wait=True)

    def split_here(self, i, at):
        """Cut a line kind in two where it was right-clicked (at: x, y on screen; near an anchor or
        polyline point: there)."""
        sh = self.shapes[i]
        roll = self.roll
        if sh["kind"] == "curve":
            c = copy.deepcopy(sh)
            seg, t, _ = nearest(sh["pts"], roll.to_xy, at.x, at.y, gaps=sh.get("gaps", ()))
            a = self.anchor_near(sh["pts"], seg, at)
            if a is None:  # a new anchor there first
                c["pts"] = split(sh["pts"], seg, t)
                for key in ("sharp", "gaps", "splits"):
                    if c.get(key):
                        c[key] = [x + 1 if x > seg else x for x in c[key]]
                c["sharp"] = sorted(set(c.get("sharp",
                                              [])) | {seg + 1})  # (a corner there, so each half keeps its shape)
                a = seg + 1
            got = split_at(c, a)
        elif sh["kind"] == "arc":
            got = self.split_arc(sh, at)
        else:
            got = self.split_line(sh, at)
        if not got:
            self.status.config(text=tr("join_split.can_t_split_there_that_s"))
            return
        self.replace_shape(i, list(got), name=tr("join_split.split_here"))
        self.status.config(text=tr("join_split.split_in_two"))
        self.tips.show("join", wait=True)

    def anchor_near(self, pts, seg, at):
        """The segment's anchor the right-click was on (near), or None."""
        d, a = min((math.hypot(self.roll.to_xy(pts[3 * a])[0] - at.x, self.roll.to_xy(pts[3 * a])[1] - at.y), a)
                   for a in (seg, seg + 1))
        return a if d <= TOUCH_PX * self.scale else None

    def nearest_on(self, pts, at):
        """The spot on the polyline pts (beat, pitch) nearest the click on screen: (segment, 0..1 along it, its
        length on screen)."""
        roll = self.roll
        p = np.array([[roll.t2x(b), roll.p2y(q)] for b, q in pts], float)
        a, ab = p[:-1], p[1:] - p[:-1]
        L = (ab ** 2).sum(1)
        u = np.clip(((at.x - a[:, 0]) * ab[:, 0] + (at.y - a[:, 1]) * ab[:, 1]) / np.where(L > 0, L, 1), 0, 1)
        d = np.hypot(a[:, 0] + ab[:, 0] * u - at.x, a[:, 1] + ab[:, 1] * u - at.y)
        j = int(np.argmin(d))
        return j, float(u[j]), float(np.sqrt(L[j]))

    def split_line(self, sh, at):
        """A line / polyline / freehand stroke cut in two: at the point right-clicked on, or on the nearest part of
        it (a freehand stroke: at its nearest drawn point; a straightened one is cut from the straightened line, so
        the halves look the same, and they're no longer straightened)."""
        pts = sh["pts"]
        if sh["kind"] == "free" and sh.get("smooth"):
            pts = [list(p) for p in smooth_path([tuple(p) for p in pts], sh["smooth"], sh.get("k", 1.0))]
            sh = {key: v for key, v in sh.items() if key != "smooth"}
        j, u, seg_px = self.nearest_on(pts, at)
        a, b = pts[j], pts[j + 1]
        near = TOUCH_PX * self.scale
        if sh["kind"] == "free":
            cut, k = (list(a), j) if u < 0.5 else (list(b), j + 1)
        elif u * seg_px <= near:
            cut, k = list(a), j
        elif (1 - u) * seg_px <= near:
            cut, k = list(b), j + 1
        else:
            cut, k = [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u], None
        if k is not None and (k in (0, len(pts) - 1) or sh["kind"] == "free" and
                              min(self.screen_dist(cut, pts[0]), self.screen_dist(cut, pts[-1])) <= near):
            return None
        left = pts[:j + 1] + [cut] if k is None else pts[:k + 1]
        right = [cut] + pts[j + 1:] if k is None else pts[k:]
        kinds = [sh["kind"]] * 2 if sh["kind"] == "free" else ["line" if len(h) == 2 else "poly" for h in (left, right)]
        return self.halves(sh, (left, right), kinds)

    def split_arc(self, sh, at):
        """An arc cut in two where it was right-clicked: two arcs on the same circle."""
        k = sh.get("k", 1.0)
        path = arc_points(sh["pts"], k)
        j, u, _ = self.nearest_on(path, at)
        f = (j + u) / (len(path) - 1)  # (arc_points: evenly round the circle)
        got = arc_circle(sh["pts"], k)

        def point(f):
            if got is None:  # (in a straight line)
                (a, _, c) = sh["pts"]
                return [a[0] + (c[0] - a[0]) * f, a[1] + (c[1] - a[1]) * f]
            centre, r, t0, turn = got
            t = t0 + turn * f
            return [(centre[0] + r * math.cos(t)) * k, centre[1] + r * math.sin(t)]
        cut = point(f)
        a, c = sh["pts"][0], sh["pts"][2]
        if min(self.screen_dist(cut, a), self.screen_dist(cut, c)) <= TOUCH_PX * self.scale:
            return None
        left, right = [list(a), point(f / 2), cut], [cut, point((1 + f) / 2), list(c)]
        return self.halves(sh, (left, right), ["arc", "arc"])

    def screen_dist(self, p, q):
        roll = self.roll
        return math.hypot(roll.t2x(p[0]) - roll.t2x(q[0]), roll.p2y(p[1]) - roll.p2y(q[1]))

    def halves(self, sh, parts, kinds):
        """The two halves of a cut shape (their points and kinds), with its other settings; the tumours stay where
        they were (tumour.split_tumour)."""
        out = []
        for half, kind in zip(parts, kinds):
            new = copy.deepcopy({key: v for key, v in sh.items() if key not in ("pts", "tumour")})
            new["pts"] = [list(p) for p in half]
            new["kind"] = kind
            out.append(new)
        tms = split_tumour(sh.get("tumour"), *(shape_path(h) for h in out))
        for new, tm in zip(out, tms):
            if tm:
                new["tumour"] = tm
        return out
