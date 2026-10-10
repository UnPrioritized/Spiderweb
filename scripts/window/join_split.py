"""Join (selected lines / polylines / freehand strokes / curves / arcs -> one curve) and Split (a joined curve back
into pieces, any of those cut in two where it was right-clicked, a custom shape into its separate drawings). The
maths is in notes/joined.py."""

import copy
import math
from tkinter import messagebox, ttk
from types import SimpleNamespace

import numpy as np

from files.lang import tr
from notes.arc import arc_circle, arc_points
from notes.convert import CAN_TURN, losses, originals, shared_settings, to_live, velocity_changed
from notes.bezier import anchor_count, nearest, split
from notes.custom import notes_shape
from notes.engine import SHAPE_DEFAULTS, as_made, cached_arrays, clean_shape, shape_path
from notes.merge import edge_moves, merged, part_rows, reshaped_parts
from notes.glue import for_part as glue_for_part, glue_box
from notes.slice import clip_segment, crossings, slice_custom
from notes.sliced import (CANT, completed, cut_in_two, keep_velocity, knife_hits, knife_in_two, moved_by, notes_across,
                          rejoined, slice_in_two, split_here_ok, tooled)
from notes.smooth import smooth_path
from notes.joined import (custom_groups, join_shapes, join_velocity, joined_end_dot, piece_velocity, sections,
                          split_at, split_custom, split_pieces)
from notes.tumour import LINE_KINDS, split_tumour
from roll.roll_shared import cached_path, cached_strokes
from window import look
from window.widgets import Tooltip

TOUCH_PX = 8  # ends closer than this on screen (times the display scaling) count as touching, like Live shape snaps


def span(sh):
    bs = [b for b, _ in cached_path(sh)]
    return min(bs), max(bs)


def last_note(olds, new):
    """"Last note: starts on it" for new (olds joined) and whether every old last note stays (joined.py)."""
    return joined_end_dot([(sh.get("end_dot"), cached_arrays(sh)) for sh in olds], cached_arrays(new),
                          olds[0].get("end_dot"))


def part_glue(part, whole):
    """A piece cut from whole keeps the glue boxes where they were in the song (only what's inside it). (A sliced
    piece of a whole shape: none, the whole's glue is done before the cut, sliced.py.)"""
    if part.get("cut") and not whole.get("cut"):
        return
    if isinstance(whole.get("glue"), list):
        got = glue_for_part(whole["glue"], glue_box(np.concatenate(cached_arrays(whole))),
                            glue_box(np.concatenate(cached_arrays(part))))
        if got:
            part["glue"] = got
        else:
            part.pop("glue", None)


JOIN_KINDS = tr("join_split.lines_polylines_freehand_strokes_curves")
LINE_FILL_TIP = tr("join_split.lines_can_t_be_filled_turn")


class JoinSplit:
    """Mixed into App."""

    def _build_line_fill(self):
        """A greyed-out Inside row for lines: says how to fill them."""
        row = self.line_fill_row = ttk.Frame(self.settings)
        ttk.Label(row, text=tr("join_split.inside"), foreground=look.FAINT_TEXT).pack(side="left")
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
        self.commit_typing()  # (a typed number: to the shapes before they're replaced)
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
        new = to_live(olds, [cached_strokes(sh) for sh in olds], self.defaults, self.custom_defaults,
                      self.roll.sy / self.roll.sx if self.roll.sx else None)
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.turn_into_live_shape"))
        self.unlink_groups(order)  # (an Add between group: plain shapes first, user)
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
        if sh["kind"] == "custom" and (sh.get("text") or "notes" in sh) and not sh.get("merge"):
            return tr("join_split.text_and_pasted_notes_can_t")
        if not self.can_split_pieces(sh):
            return (tr("join_split.it_s_all_one_piece_nothing") if sh["kind"] in ("curve", "custom")
                    else tr("join_split.only_joined_curves_and_custom_shapes"))
        return None

    def split_selected(self):
        self.commit_typing()  # (a typed number: to the shape before it's replaced)
        problem = self.split_problem()
        if problem:  # (the shortcut: say why)
            self.status.config(text=problem)
        else:
            self.split_pieces(self.sel)

    def can_join(self):
        return self.join_problem() is None

    def join_selected(self):
        self.commit_typing()  # (a typed number: to the shapes before they're replaced)
        problem = self.join_problem()
        if problem:  # (the shortcut: say why)
            self.status.config(text=problem)
            return
        if self.roll.sx is None:
            return
        roll = self.roll

        def touch(p, q):
            return math.hypot(roll.t2x(p[0]) - roll.t2x(q[0]), roll.p2y(p[1]) - roll.p2y(q[1])) <= TOUCH_PX * self.scale

        def joined(shapes):
            new = join_shapes(shapes, roll.sy / roll.sx, touch)
            if new is not None:
                new.update(shared_settings(shapes)[0])  # (glue / chop / claw / strum / Colours: kept when all alike)
                join_velocity(new, shapes, [span(sh) for sh in shapes], span(new))  # (each keeps its velocities)
                new["end_dot"] = last_note(shapes, new)[0]
            return new

        order = sorted(self.sels)
        olds = [self.shapes[i] for i in order]
        new = joined(olds)
        if new is None:
            return
        again = rejoined(olds, new, joined)  # (pieces of one shape: it again, or a bigger piece of it)
        if again == CANT:
            self.status.config(text=tr("join_split.cant_rejoin"))
            return
        lost = shared_settings(olds)[1]
        if not again and not last_note(olds, new)[1]:
            lost.append(tr("join_split.last_note_moves"))
        if lost and not messagebox.askokcancel(
                tr("join_split.spiderweb"), tr("join_split.joining_these_changes") + "\n• ".join(lost) +
                tr("join_split.ctrl_z_gives_them_back"), icon="warning", parent=self):
            return
        new = again or new
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.join"))
        self.unlink_groups(order)
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

    def merge_pair(self):
        """The two selected shapes Gate sensitive merge can use, or None."""
        sels = sorted(i for i in self.sels if i < len(self.shapes))
        return sels if len(sels) == 2 else None

    def gate_merge(self, to_right):
        """Right-click → Gate sensitive merge ▸ Merge to right / left (user; no anchor, anywhere in the menu works):
        to the right = the shape on the left slides right, each key row until it meets the other shape's notes;
        to the left = the shape on the right slides left (notes/merge.py). Both become one custom shape of plain
        notes (Ctrl+Z: both again). Rows with nothing to meet slide by the smallest slide and become a shape of
        their own right after it (user: to delete or keep later)."""
        self.commit_typing()  # (a number typed in a panel box: to the two shapes before they're replaced)
        pair = self.merge_pair()
        if not pair:
            return
        if any("picture" in self.shapes[i] for i in pair):  # (user: an image keeps its own colours, so not merged)
            self.status.config(text=tr("join_split.merge_no_image"))
            return
        got = merged(part_rows([self.shapes[i] for i in pair], self.ppq, self.keys), to_right, self.ppq, self.keys)
        if got is None:
            self.status.config(text=tr("join_split.merge_no_meet"))
            return

        def shape(rows, name):  # (track = each shape's own colours, merge.part_rows)
            notes = np.column_stack([rows[:, 0], rows[:, 1] - rows[:, 0], rows[:, 2], rows[:, 3], rows[:, 4]])
            new = {**SHAPE_DEFAULTS, **self.defaults, **notes_shape(notes, self.ppq, name)}
            new.pop("cycle", None)  # (Colours for new shapes: not on it, its shapes keep their own colours)
            return clean_shape(new)

        out, rest = got
        new = [shape(out, tr("join_split.merged"))] + ([shape(rest, tr("join_split.merge_leftovers"))] if len(rest)
                                                        else [])
        parts = [copy.deepcopy(self.shapes[i]) for i in pair]
        for p in parts:
            p.pop("between", None)  # (its Add between group is unlinked)
        new[0]["merge"] = {"parts": parts, "right": bool(to_right),
                           "ppq": self.ppq, "keys": self.keys, "apart": sorted(set(rest[:, 2].tolist())),
                           "at": list(new[0]["pts"][0]),  # (where its box was: Split moves the parts as far as it)
                           "box": [list(p) for p in new[0]["pts"]]}  # (turned / slanted since: the parts too)
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.merge"))
        self.unlink_groups(pair)
        for i in reversed(pair):
            del self.shapes[i]
        self.shapes[pair[0]:pair[0]] = new
        self.select(pair[0])  # (the merged shape, user: leftovers are dealt with later)
        self.shapes_changed()
        self.status.config(text=tr("join_split.merged_left", n=len(np.unique(rest[:, 2]))) if len(rest)
                           else tr("join_split.merged_done"))

    def pieces(self):
        """The selected shapes that are pieces cut from another shape, keeping their notes (sliced.py)."""
        return [i for i in sorted(self.sels) if i < len(self.shapes) and self.shapes[i].get("cut")
                and moved_by(as_made(self.shapes[i])) is not None]

    def make_complete(self):
        """Right-click → Turn into a complete shape (user): the selected pieces forget the shape they were cut from,
        their notes follow their own lines from now on (Ctrl+Z: pieces again)."""
        got = self.pieces()
        if not got:
            return
        self.push_undo(name=tr("join_split.make_complete"))
        for i in got:
            completed(self.shapes[i])
        self.shapes_changed()
        self.status.config(text=tr("join_split.made_complete") if len(got) == 1 else
                           tr("join_split.made_complete_n", n=len(got)))

    def can_split_pieces(self, sh):
        """A joined curve with more than one piece / shape in it, a live shape that can go back to the shapes it was
        made of, a Gate sensitive merge, or a custom drawing with separate parts."""
        if sh["kind"] == "custom" and (originals(sh) or sh.get("merge")):
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
        self.unlink_groups([i])
        for p in parts:
            p.pop("between", None)
        whole = span(old)
        for p in parts if velocity else ():
            piece_velocity(p, old, span(p), whole)
            part_glue(p, old)
            keep_velocity(p)
        self.shapes[i:i + 1] = parts
        self.select_many(range(i, i + len(parts)), i)
        self.shapes_changed()

    def split_pieces(self, i):
        sh = self.shapes[i]
        if not self.can_split_pieces(sh):
            return
        if sh.get("merge"):  # Gate sensitive merge: the two shapes as they were before it, moved along with it
            m = sh["merge"]
            got = reshaped_parts(sh)
            if got:  # (turned / slanted / stretched: the parts that way, the sliding one where it slid)
                back = got[0]
            else:
                db, dp = (sh["pts"][0][0] - m["at"][0], sh["pts"][0][1] - m["at"][1]) if m.get("at") else (0, 0)
                back = copy.deepcopy(m["parts"])
                for p in back:
                    p["pts"] = [[b + db, q + dp] for b, q in p["pts"]]
            if not sh.get("own_vel"):  # (its velocity was changed: each shape takes the line where its notes sat)
                moves = [(0.0, 0.0)] * 2 if got else edge_moves(m)
                for p, (d0, d1) in zip(back, moves):
                    lo, hi = span(p)
                    piece_velocity(p, sh, (lo + d0, hi + d1), span(sh))
                    p.pop("own_vel", None)
            self.replace_shape(i, back, velocity=False, name=tr("join_split.split_back_into_the_old_shapes"))
            self.status.config(text=tr("join_split.back_to_the_shapes_it_was", n=2))
            return
        back = originals(sh) if sh["kind"] == "custom" else None
        if back:  # the shapes it was made of (Turn into live shape), as they were
            if velocity_changed(sh):  # (with the velocities it was given since, each over its own time)
                whole = span(sh)
                for p in back:
                    piece_velocity(p, sh, span(p), whole)
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
        if not split_here_ok(self.shapes[i]):
            self.status.config(text=tr("join_split.split_here_use_slice"))
            return
        got = self.cut_at(self.shapes[i], at)
        if not got:
            self.status.config(text=tr("join_split.can_t_split_there_that_s"))
            return
        self.replace_shape(i, list(got), name=tr("join_split.split_here"))
        self.status.config(text=tr("join_split.split_in_two"))
        self.tips.show("join", wait=True)

    def cut_at(self, sh, at, mark=None):
        """A line kind cut in two at the spot at (x, y on screen) -> its two halves, or None (too near an end).
        They're pieces that keep their notes (sliced.py); mark: the cut's mark ({"kind": "slice", "dir": ...}; default
        Split here's scissors)."""
        got = self._cut_at(sh, at)
        if got:
            roll = self.roll
            scale = (abs(roll.t2x(1) - roll.t2x(0)), abs(roll.p2y(0) - roll.p2y(1)))
            cut_in_two(sh, got, got[0]["pts"][-1], scale, mark or {"kind": "split"})
        return got

    def _cut_at(self, sh, at):
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
        return list(got) if got else None

    def slice_along(self, a, b):
        """The Slice tool's line from a to b (beat, key) let go: every line kind it crosses cut there, every custom
        shape it goes all the way across cut in two (slice.py); a shape with note tool pages / glue: its notes, where
        it goes all the way across them (sliced.knife_in_two). With Select boxes kept: only the shapes they
        selected, and only inside the boxes. One undo step; the pieces end up selected."""
        roll = self.roll
        boxes = roll.kept_box()
        segs = [(a, b)] if not boxes else [s for s in (clip_segment(a, b, box) for box in boxes) if s]
        targets = sorted(self.sels) if boxes else range(len(self.shapes))
        done, out, skipped = {}, {}, 0
        for i in targets:
            sh = self.shapes[i]
            if sh["kind"] in ("custom", "funnel") and ("notes" in sh or sh.get("text") or sh.get("hz")):
                continue
            if (sh["kind"] == "custom" or sh["kind"] in LINE_KINDS) and tooled(sh) or sh["kind"] == "funnel":
                # (pages / glue: its notes are cut, wherever they are; both pieces keep the drawing, user. A funnel:
                # always its notes, each piece drawn only on its side: sliced.piece_knives)
                pieces, crossed = [sh], False
                for sa, sb in segs:
                    nxt = []
                    for p in pieces:
                        stretch, hit = notes_across(self.notes_of(p), sa, sb, self.ppq)
                        crossed |= hit
                        if stretch:
                            got = [copy.deepcopy(p), copy.deepcopy(p)]
                            hits = (knife_hits(self.notes_of(p), sa, sb, self.ppq) if sh["kind"] in LINE_KINDS
                                    else ())
                            knife_in_two(p, got, sa, sb, stretch, hits)
                        nxt += got if stretch else [p]
                    pieces = nxt
                if len(pieces) == 1 and crossed:
                    skipped += 1
            elif sh["kind"] == "custom":
                pieces = [sh]
                for sa, sb in segs:
                    nxt = []
                    for p in pieces:
                        got = slice_custom(p, sa, sb, self.ppq)
                        if got:  # (pieces keeping the notes on their side, sliced.py)
                            slice_in_two(p, got, sa, sb)
                        nxt += got if got else [p]
                    pieces = nxt
                if len(pieces) == 1 and any(crossings(np.concatenate(cached_arrays(sh)), sa, sb) for sa, sb in segs):
                    skipped += 1  # (crossed, but not all the way across)
            elif sh["kind"] in LINE_KINDS:
                pieces = [sh]
                for sa, sb in segs:
                    for pt, _ in crossings(cached_path(sh), sa, sb):
                        at = SimpleNamespace(x=roll.t2x(pt[0]), y=roll.p2y(pt[1]), state=0)
                        # (the piece the crossing is on)
                        k = min(range(len(pieces)), key=lambda j: self.path_dist(pieces[j], at))
                        got = self.cut_at(pieces[k], at, {"kind": "slice", "dir": [sb[0] - sa[0], sb[1] - sa[1]]})
                        if got:
                            pieces[k:k + 1] = got
            else:
                continue
            if len(pieces) > 1:
                done[i] = pieces
        if not done:
            self.status.config(text=tr("join_split.slice_nothing") if not skipped else tr("join_split.slice_partly"))
            return
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.slice"))
        self.unlink_groups(done)
        for parts in done.values():
            for p in parts:
                p.pop("between", None)
        new, picked = [], []
        for i, sh in enumerate(self.shapes):
            parts = done.get(i, [sh])
            whole = span(sh)
            for p in parts if i in done else ():
                piece_velocity(p, sh, span(p), whole)
                part_glue(p, sh)
                keep_velocity(p)
            if i in done:
                picked += range(len(new), len(new) + len(parts))
            new += parts
        self.shapes[:] = new
        self.select_many(picked, picked[0])
        self.shapes_changed()
        n = sum(len(p) for p in done.values())
        self.status.config(text=tr("join_split.sliced", shapes=len(done), n=n) +
                           (tr("join_split.slice_partly_too", n=skipped) if skipped else ""))

    def path_dist(self, sh, at):
        """How far (on screen) the shape's line passes from at."""
        p = np.asarray(cached_path(sh), float).reshape(-1, 2)
        x, y = self.roll.t2x(p[:, 0]), self.roll.p2y(p[:, 1])
        if len(p) < 2:
            return float(np.hypot(x - at.x, y - at.y).min())
        ax, ay, bx, by = x[:-1], y[:-1], x[1:] - x[:-1], y[1:] - y[:-1]
        L = bx * bx + by * by
        u = np.clip(((at.x - ax) * bx + (at.y - ay) * by) / np.where(L > 0, L, 1), 0, 1)
        return float(np.hypot(ax + bx * u - at.x, ay + by * u - at.y).min())

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
