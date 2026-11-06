"""Join (selected lines / polylines / freehand strokes / curves / arcs -> one curve) and Split (a joined curve back
into pieces, any of those cut in two where it was right-clicked, a custom shape into its separate drawings). The
maths is in notes/joined.py."""

import copy
import math
import os
import uuid
from tkinter import messagebox, ttk
from types import SimpleNamespace

import numpy as np

from files.lang import tr, trn
from notes.arc import arc_circle, arc_points
from notes.between import group_of, turns as between_turns
from notes.convert import CAN_TURN, losses, originals, shared_settings, to_live, velocity_changed
from notes.bezier import nearest, split
from notes.custom import notes_shape, pack_notes, unpack_notes
from notes.fillgaps import fill_gaps as filled, needs_fill
from notes.lines import flipped_lines, lined_up, note_lines, overlap_losers
from notes.passengers import has_cc, pack_cc, split_cc, unpack_cc
from files.own_tracks import COUNT_MOST, FILE0, clean_name, from_rows, full_plan, into_ids, new_id, with_port
from notes.engine import (PORTS, SHAPE_DEFAULTS, as_made, cached_arrays, channels_of, clean_shape, shape_notes_tracks,
                          shape_path)
from notes.gateflip import lifted, put_back, turn_velocities
from notes.loaded import clean_extract, extract_groups, held_extract
from notes.mask import SKIP10, is_mask, same_mask
from notes.sustain import alike
from notes.engine import free_box
from notes.custom import ROUND
from notes.recolour import KEEP, box_of, carried, colour_sets, is_recoloured, turn_again, without_recolour
from notes.merge import (LAP, box_map, edge_moves, low_corner, merge_parts, merged, merged_up_down, mover,
                         part_rows, reshaped_parts, side_plan, spans_of, unmerged, up_down_plan, up_down_rows)
from notes.glue import for_part as glue_for_part, glue_box
from notes.pattern import bake_shape, has_formula
from notes.lasso import inside_point, knife_spans, lasso_notes, loop_crossings, path_middle, takes, touches
from notes.slice import EPS, clip_segment, crossings, slice_custom, slice_notes
from notes.sliced import (CANT, completed, cut_in_two, keep_velocity, keep_was, knife_hits, knife_in_two, moved_by,
                          notes_across, rejoined, slice_in_two, split_here_ok, tooled)
from notes.smooth import smooth_path
from notes.joined import (custom_groups, join_shapes, join_velocity, joined_end_dot, piece_velocity, sections,
                          split_at, split_custom, split_pieces)
from notes.tumour import LINE_KINDS, split_tumour
from roll.roll_shared import cached_path, cached_strokes
from window import look
from notes.subtract import plain_subtract, same_subtract
from window.subtract_window import carry_subtract
from window.extend_window import carry_extend
from window.sustain_window import carry_stops
from window.widgets import Tooltip

TOUCH_PX = 8  # ends closer than this on screen (times the display scaling) count as touching, like Live shape snaps
ON_PX = 1e-6  # Slice / Lasso cuts a line where their line crosses it: onto a corner only when right on it


def multi_slots(rows, multi):
    """A shape's rendered rows with each note's slot from its rows in Multi channel (multi; Turn into plain notes in
    Single channel / As drawn: Multi channel later shows its colours, hunt): the note starting there on its key, else
    the slot most of its notes have."""
    rows = rows.copy()
    if not len(multi):
        return rows
    most = int(np.bincount(multi[:, 4]).argmax())
    have = multi[:, 0].astype(np.int64) * 1024 + multi[:, 2]  # (start and key as one number: keys < 1024)
    o = np.argsort(have, kind="stable")
    have, slot = have[o], multi[o, 4]
    want = rows[:, 0].astype(np.int64) * 1024 + rows[:, 2]
    at = np.searchsorted(have, want, side="right") - 1  # (two notes starting on one spot: the later one, as before)
    found = (at >= 0) & (have[np.maximum(at, 0)] == want)
    rows[:, 4] = np.where(found, slot[np.maximum(at, 0)], most)
    return rows


def by_owner(rows):
    """rows (rendered's columns) -> shape number -> its rows, in their order (sorted once, sliced out)."""
    o = np.argsort(rows[:, 5], kind="stable")
    rows = rows[o]
    owners = rows[:, 5]
    return lambda i: rows[np.searchsorted(owners, i):np.searchsorted(owners, i, side="right")]


def line_tracks(owner, slot):
    """The line tools' track per note: a shape's own colour (lines never cross shapes or colours). Slots stay under
    2 ** 40 (own tracks' OWN0 = 2 ** 32 + id x 16)."""
    return np.asarray(owner, np.int64) << 40 | np.asarray(slot, np.int64)


def without10(sh, use10):
    """A Turn into plain notes shape keeps channel 10 in use only if one of its notes is on it (hunt): else its tracks
    are made as with channel 10 skipped (the same channels), so unticking Use channel 10 drops it."""
    rows = unpack_notes(sh["notes"])
    on10 = bool(use10 and len(rows) and (rows[:, 4] % 16 == 9).any())
    if use10 and len(rows) and not on10:
        rows = rows.copy()
        t = rows[:, 4]
        back = lambda t: t // 16 * 15 + np.array([SKIP10.index(c) if c != 9 else 0 for c in range(16)])[t % 16]
        rows[:, 4] = back(t)
        sh["notes"] = pack_notes(rows)
        if "cc" in sh:  # (its CCs' tracks the same way, hunt 2026-10-22)
            ev = unpack_cc(sh["cc"])
            ks = back(np.array([e[1] for e in ev], np.int64)).tolist()
            sh["cc"] = pack_cc([(e[0], k, e[2], e[3]) for e, k in zip(ev, ks)])
    sh["slots10"] = on10  # (asked before renumbering: channel 11 becomes slot 9 there, user 2026-10-20)
    return sh


def span(sh):
    bs = [b for b, _ in cached_path(sh)]
    return min(bs), max(bs)


def last_note(olds, new):
    """"Last note: starts on it" for new (olds joined) and whether every old last note stays (joined.py)."""
    return joined_end_dot([(sh.get("end_dot"), cached_arrays(sh)) for sh in olds], cached_arrays(new),
                          olds[0].get("end_dot"))


def carry_recolour(app, whole, parts):
    """Pieces of a recoloured drawn shape: each one's Recolour remembers the whole's box (recolour.carried), so it
    keeps the colours it had in the whole however it's moved, flipped or changed later. Changes parts."""
    if not is_recoloured(whole):
        return

    def box(s):
        s = without_recolour(s)
        notes, _ = app.notes_tracks(s)
        return (free_box(s, notes, app.notes_tracks, app.keys) or box_of(notes)) if len(notes) else None
    w = box(whole)
    for p in parts if w else ():
        b = box(p) if isinstance(p.get("recolour"), dict) else None
        if b:
            p["recolour"] = carried(whole["recolour"], w, b)


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
            from notes.fx import tidy_glue
            tidy_glue(part)


JOIN_KINDS = tr("join_split.lines_polylines_freehand_strokes_curves")
LINE_FILL_TIP = tr("join_split.lines_can_t_be_filled_turn")


class JoinSplit:
    """Mixed into App."""

    def _build_line_fill(self):
        """A greyed-out Inside row for lines: says how to fill them."""
        row = self.line_fill_row = ttk.Frame(self.settings)
        lb = ttk.Label(row, text=tr("join_split.inside"), foreground=look.FAINT_TEXT)
        lb.pack(side="left")
        Tooltip(lb, LINE_FILL_TIP)  # (one on each part: one on the row too showed twice over the box)
        # one greyed dropdown showing Empty, like a custom shape's Inside (user 2026-10-28: the old radios)
        box = ttk.Combobox(row, state="disabled", width=12,
                           values=(tr("join_split.empty"), tr("join_split.fill"), tr("join_split.spam"),
                                   tr("join_split.outline_spam")))
        box.current(0)
        box.pack(side="left", padx=(5, 0))
        Tooltip(box, LINE_FILL_TIP)

    def sync_line_fill(self):
        self._rows["line_fill"] = bool(self.sels) and all(
            i < len(self.shapes) and self.shapes[i]["kind"] in LINE_KINDS for i in self.sels)
        self.layout_rows()

    def kind_word(self, sh):
        """What a shape is, for "(... selected)" reasons: plain notes made from a shape = pasted notes (hunt: said
        "custom", from its name "Custom: Circle (notes)")."""
        if sh.get("made") == "plain" and not sh.get("merge") and not sh.get("unmerge"):
            return tr("app.pasted_notes")
        return self.plain_label(sh).split(":")[0]

    def live_problem(self):
        """Why the selection can't be turned into a live shape (None = it can)."""
        sels = [i for i in self.sels if i < len(self.shapes)]
        if not sels:
            return tr("join_split.select_the_shapes_to_turn_into", JOIN_KINDS=JOIN_KINDS)
        shapes = [self.shapes[i] for i in sels]
        if any(sh["kind"] not in CAN_TURN or sh.get("text") or "notes" in sh for sh in shapes):
            other = sorted({self.kind_word(sh) for sh in shapes
                            if sh["kind"] not in CAN_TURN or sh.get("text") or "notes" in sh})
            return tr("join_split.only_and_custom_shapes_can_be", JOIN_KINDS=JOIN_KINDS,
                      join=tr("roll_funnel.and").join(other).lower())
        if all(sh["kind"] == "custom" for sh in shapes) and len(shapes) == 1:
            return tr("join_split.it_s_a_custom_shape_already")
        return None

    def shared_into(self, olds):
        """(the tracks every one of olds was laid on (Draw into a track), or [] when not all on the same ones; True if
        any of them was laid on tracks but they differ: the new shape goes automatic, the warning says so)."""
        from window.app import placed_on
        own = self.own_channels()
        laid = [placed_on(sh, own) for sh in olds]
        if laid[0] and all(p == laid[0] for p in laid):
            return laid[0], False
        return [], any(laid)

    def keep_into(self, news, laid, first):
        """Join / Turn into live shape / Gate sensitive merge keep the tracks their shapes shared (hunt 2026-10-26:
        they went automatic): news laid on laid (shared_into; [] = automatic), first's channel-10 rule kept."""
        from window.app import can_go_in
        for sh in news:
            for key in ("into", "cols", "into_gap", "into_rest", "slots10", "into_new"):
                sh.pop(key, None)
            if laid and can_go_in(sh):
                sh["into"] = list(laid)
                if isinstance(first.get("slots10"), bool):
                    sh["slots10"] = first["slots10"]

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
        laid, mixed = self.shared_into(olds)
        if mixed:
            lost.append(tr("join_split.leaves_tracks"))
        if lost and not messagebox.askokcancel(
                tr("join_split.spiderweb"), tr("join_split.turning_these_into_a_live_shape") + "\n• ".join(lost) +
                tr("join_split.split_into_separate_shapes_or_ctrl"), icon="warning", parent=self):
            return
        new = to_live(olds, [cached_strokes(sh) for sh in olds], self.defaults, self.custom_defaults,
                      self.roll.sy / self.roll.sx if self.roll.sx else None)
        self.keep_into([new], laid, olds[0])
        carry_stops(self, olds, [new])  # (Sustain's stops put by hand: on the same ticks)
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
        other = sorted({self.kind_word(self.shapes[i]) for i in sels
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
        if sh["kind"] == "custom" and (sh.get("text") or "notes" in sh) and not merge_parts(sh):
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

    def join_fn(self):
        """Join's way of making one shape of several: joined(shapes) -> the new shape or None."""
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
        return joined

    def joined_shape(self, olds):
        """Pieces of one shape that meet joined back into one piece (as Join does), or None when they can't be."""
        joined = self.join_fn()
        new = joined(olds)
        if new is None:
            return None
        again = rejoined(olds, new, joined)
        return None if again == CANT or not again else again

    def join_selected(self):
        self.commit_typing()  # (a typed number: to the shapes before they're replaced)
        problem = self.join_problem()
        if problem:  # (the shortcut: say why)
            self.status.config(text=problem)
            return
        if self.roll.sx is None:
            return
        joined = self.join_fn()
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
        laid, mixed = self.shared_into(olds)
        if mixed:
            lost.append(tr("join_split.leaves_tracks"))
        if lost and not messagebox.askokcancel(
                tr("join_split.spiderweb"), tr("join_split.joining_these_changes") + "\n• ".join(lost) +
                tr("join_split.ctrl_z_gives_them_back"), icon="warning", parent=self):
            return
        if not again:
            carry_stops(self, olds, [new])
        new = again or new
        self.keep_into([new], laid, olds[0])
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
        """The selected shapes Gate sensitive merge can use (2 or more: "Keep shapes separate" takes any number, hunt),
        or None."""
        sels = sorted(i for i in self.sels if i < len(self.shapes))
        return sels if len(sels) >= 2 else None

    def merge_sels(self):
        """merge_pair, or None when fewer than 2 are left without "Merge leftovers" whose Merged notes is selected too
        (after a merge all stay selected: they're part of it, merge_own)."""
        sels = self.merge_pair() or []
        return sels if len(sels) - sum(map(len, self.merge_own(sels).values())) >= 2 else None

    def merge_own(self, sels):
        """{place in sels of a Merged notes: places of its own "Merge leftovers" selected with it}: never moved by
        themselves; no wall when it moves (hunt), a wall when another shape moves (user 2026-10-19: notes went
        behind them)."""
        ids = {(self.shapes[i].get("unmerge") or {}).get("id"): k for k, i in enumerate(sels)}
        ids.pop(None, None)
        own = {}
        for k, i in enumerate(sels):
            m = ids.get(self.shapes[i].get("leftover_of"))
            if m is not None:
                own.setdefault(m, set()).add(k)
        return own

    def merge_problem(self, way, apart):
        """Why Gate sensitive merge can't go the way picked (the window's hint, greyed Merge), or None."""
        sels = self.merge_sels() if apart else self.merge_pair()
        if not sels:
            return tr("merge_window.need_two")
        if not apart and len(sels) > 2:
            return tr("join_split.merge_two_only")
        if any("picture" in self.shapes[i] for i in sels):  # (user: an image keeps its own colours, so not merged)
            return tr("join_split.merge_no_image")
        if apart:
            # (a mask's notes are a preview, not something to keep or to meet: a silent one was a wall, hunt)
            if any(is_mask(self.shapes[i]) for i in sels):
                return tr("join_split.merge_no_mask")
            if self.merge_mover(sels, way)[0] is None:
                return tr("join_split.merge_no_notes")
            picked = self.merge_pick(sels, way)  # (said before Merge is pressed, hunt)
            self._merge_tried = (("pick", tuple(sels), way), picked)
            got = picked[1]
            if isinstance(got, str):
                return got
            return None if got["moved"] else tr("join_split.merge_nothing_moved")
        if len({is_mask(self.shapes[i]) for i in sels}) > 1:  # (its preview notes would become heard notes, hunt)
            return tr("join_split.merge_mask_mixed")
        got = self.one_merge(sels, way)
        self._merge_tried = (("one", tuple(sels), way), got)
        return got if isinstance(got, str) else None

    def merge_tried(self, key):
        """What merge_problem just worked out for key (gate_merge asks it right before: not made twice), or None."""
        got, self._merge_tried = getattr(self, "_merge_tried", None), None
        return got[1] if got and got[0] == key else None

    def merge_pick(self, sels, way):
        """(which of sels moves, merge_slide's plan for it or the message why it can't): a Merged notes made this way
        moves again only if it would move, else the others' own mover meets it (hunt)."""
        k, alt, notes = self.merge_mover(sels, way)
        own = self.merge_own(sels)
        got = self.merge_slide(notes, k, way, own.get(k, ()))
        if alt is not None and (isinstance(got, str) or not got["moved"]):
            other = self.merge_slide(notes, alt, way, own.get(alt, ()))
            if not isinstance(other, str) and other["moved"]:
                k, got = alt, other
        return k, got

    def one_merge(self, pair, way):
        """The one-shape merge of the two shapes in pair: (their parts, their gate flips (gateflip.lifted), (merged
        rows, leftovers), which part moved (up / down), part_rows (+ each note's colour round with "New channels
        each Colours round")), or the message why it can't."""
        parts = [copy.deepcopy(self.shapes[i]) for i in pair]
        flips = lifted(parts)  # (both gate flipped: the merged shape is, each row across both, user 2026-10-11)
        rows = part_rows(parts, self.ppq, self.keys, self.rounds_apart())
        if way in ("up", "down"):
            slide = mover(rows, way)  # (kept in the recipe)
            got = merged_up_down(rows, way == "up", self.keys, slide, need_room=True)
            if got is False:  # (like Keep shapes separate, hunt)
                return tr("join_split.merge_no_room_" + way)
        else:
            slide, got = None, merged(rows, way == "right", self.ppq, self.keys)
        if got is None:
            return tr("join_split.merge_no_meet_" + way if slide is not None else "join_split.merge_no_meet")
        return parts, flips, got, slide, rows

    def merge_mover(self, sels, way):
        """(Which of sels moves (place in sels; None when a shape has no notes), the one moving instead when that's a
        Merged notes which wouldn't move (or None), each one's notes): a Merged notes made this way before moves
        again (user 2026-10-19) if it would move, else merge.mover among the others (hunt: a new shape meets it)."""
        notes = [np.asarray(self.notes_of(self.shapes[i]), np.int64).reshape(-1, 4) for i in sels]
        if not all(len(n) for n in notes):
            return None, None, notes
        lone = set().union(*self.merge_own(sels).values())  # (leftovers never move by themselves)
        again = [k for k, i in enumerate(sels) if (self.shapes[i].get("unmerge") or {}).get("way") == way]
        if len(again) == 1:
            return again[0], mover(notes, way, skip=lone | {again[0]}), notes
        return mover(notes, way, skip=lone), None, notes

    def gate_merge(self, to_right, apart=False):
        """Right-click → Gate sensitive merge ▸ Merge to right / left (user; no anchor, anywhere in the menu works):
        to the right = the shape on the left slides right, each key row until it meets the other shape's notes;
        to the left = the shape on the right slides left (notes/merge.py). Both become one custom shape of plain
        notes (Ctrl+Z: both again). Rows with nothing to meet slide by the smallest slide and become a shape of
        their own right after it (user: to delete or keep later). apart ("..., keep shapes separate"): merged_apart.
        to_right "up" / "down": merged_up_down. Asked from the Gate sensitive merge window (merge_window.py)."""
        self.commit_typing()  # (a number typed in a panel box: to the two shapes before they're replaced)
        pair = self.merge_pair()
        way = to_right if to_right in ("up", "down") else "right" if to_right else "left"
        problem = self.merge_problem(way, apart)
        if problem:
            self.status.config(text=problem)
            return
        if apart:
            return self.merged_apart(self.merge_sels(), way)
        updown = way in ("up", "down")
        # (merge_problem tried it: it can merge)
        parts, flips, got, slide, _ = self.merge_tried(("one", tuple(pair), way)) or self.one_merge(pair, way)

        def shape(rows, name, made=None, lapped=False):  # (track = each shape's own colours, merge.part_rows)
            notes = np.column_stack([rows[:, 0], rows[:, 1] - rows[:, 0], rows[:, 2], rows[:, 3], rows[:, 4]])
            new = {**SHAPE_DEFAULTS, **self.defaults, **notes_shape(notes, self.ppq, name)}
            new.pop("cycle", None)  # (Colours for new shapes: not on it, its shapes keep their own colours)
            if made:
                new["made"] = made  # (its name in any language)
            if lapped:
                new["lapped"] = True
            return clean_shape(new)

        out, rest = got
        lapped = rest.shape[1] > 5  # ("New channels each Colours round": each note's round on its track, + LAP x round:
        # a plain shape would take the first channels and push the merged one a round on, hunt; engine "lapped")
        if lapped:
            rest = np.column_stack([rest[:, :4], rest[:, 4] + rest[:, 5] * LAP])
        new = [shape(out, tr("join_split.merged"))]
        if len(rest):
            new.append(shape(rest, tr("join_split.merge_leftovers"), "leftovers", lapped))
            ex = clean_extract([g for i in pair for g in extract_groups(held_extract(self.shapes[i]))])
            if ex:  # (its notes taken from a loaded MIDI stay out of it until both shapes are gone, hunt 2026-10-25)
                new[-1]["extract"] = ex
        sus = [self.shapes[i].get("sustain") for i in pair]
        if sus[0] is not None and alike(sus[0]) == alike(sus[1]):  # (both sustained alike: so are the merged notes,
            for sh in new:                                          # like Join; stops put by hand: on their ticks)
                sh["sustain"] = copy.deepcopy(sus[0])
            carry_stops(self, [self.shapes[i] for i in pair], new)
        subs = [self.shapes[i].get("subtract") for i in pair]
        if subs[0] is not None and subs[1] is not None and same_subtract(*subs):  # (both alike: so are the merged
            for sh in new:                                                        # notes)
                sh["subtract"] = plain_subtract(copy.deepcopy(subs[0]))
        exts = [self.shapes[i].get("extend") for i in pair]
        if exts[0] is not None and exts[0] == exts[1]:  # (Gate extend the same way)
            for sh in new:
                sh["extend"] = copy.deepcopy(exts[0])
        masks = [self.shapes[i].get("mask") for i in pair]
        if masks[0] and masks[1] and same_mask(*masks):  # (both masks alike: so is what's made of them, like Join;
            # groups apart, the first one's)
            for sh in new:
                sh["mask"] = copy.deepcopy(masks[0])
        for p in parts:
            p.pop("between", None)  # (its Add between group is unlinked)
        new[0]["merge"] = {"parts": parts, "right": bool(to_right) and not updown,
                           "ppq": self.ppq, "keys": self.keys,
                           "apart": [] if updown else sorted(set(rest[:, 2].tolist())),
                           "at": list(new[0]["pts"][0]),  # (where its box was: Split moves the parts as far as it)
                           "box": [list(p) for p in new[0]["pts"]]}  # (turned / slanted since: the parts too)
        if updown:  # (up / down, user 2026-10-19: the leftovers' times, so a remake leaves them out)
            new[0]["merge"].update(way=way, slide=int(slide),
                                   spans=[[a / self.ppq, b / self.ppq] for a, b in spans_of(rest)])
        if flips:
            new[0]["merge"]["flips"] = flips
            for sh in new:  # (the leftovers too: they were flipped)
                sh["gate_flip"] = True
        self.keep_into(new, self.shared_into([self.shapes[i] for i in pair])[0], self.shapes[pair[0]])
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.merge"))
        self.unlink_groups(pair)
        for i in reversed(pair):
            del self.shapes[i]
        self.shapes[pair[0]:pair[0]] = new
        self.select(pair[0])  # (the merged shape, user: leftovers are dealt with later)
        self.shapes_changed()
        rows = len(np.unique(rest[:, 2])) if len(rest) else 0
        if updown:
            self.status.config(text=tr("join_split.merged_one_" + way + "_wall") if len(rest)
                               else tr("join_split.merged_one_up_down_done"))
        else:
            self.status.config(text=trn("join_split.merged_left", rows, n=rows) if len(rest)
                               else tr("join_split.merged_done"))

    def merged_apart(self, sels, way):
        """gate_merge's "Keep shapes separate" (user 2026-10-19), any number of shapes: one moves (merge_mover), the
        others stay as they are and are what it meets. Right / left: each key row slides until it meets their notes;
        up / down: cut where their notes start and end, each piece slides until it meets them (merge.up_down_plan).
        It becomes fixed notes exactly as they sounded, "Merged notes"; what had nothing to meet = a "Merge
        leftovers" shape of its own (user, hunt: to delete later). Split gives back the shape it was (unmerged)."""
        k, got = self.merge_tried(("pick", tuple(sels), way)) or self.merge_pick(sels, way)
        if isinstance(got, str):
            self.status.config(text=got)
            return
        if not got["moved"]:  # (already touching: no step, nothing frozen, hunt)
            self.status.config(text=tr("join_split.merge_nothing_moved"))
            return
        i = sels[k]
        stays = [j for j in sels if j != i]
        slid_rows, late, slid, kslid = got["rows"], got["late"], got["slid"], got["kslid"]
        left = []

        def part(rest):
            def pick(rows):
                rows, lone = slid_rows(rows)
                if rest:
                    left.append(rows[lone])
                return rows[lone == rest]
            return pick
        new = self.plain_copies([i], part(False), own=True).get(i)
        if new is None:
            self.status.config(text=tr("join_split.merge_no_meet"))
            return
        rest = self.plain_copies([i], part(True), own=True).get(i)
        self.put_merged(i, new, rest, stays, late, way, slid, kslid)
        if way in ("up", "down"):
            self.status.config(text=tr("join_split.merged_" + way + "_wall") if rest is not None
                               else tr("join_split.merged_up_down_done"))
        else:
            rows = len(np.unique(left[0][:, 2])) if left and rest is not None else 0
            self.status.config(text=trn("join_split.merged_apart_left", rows, n=rows) if rows
                               else tr("join_split.merged_apart_done"))

    def merge_slide(self, notes, k, way, skip=()):
        """How shape k of notes (each selected shape's own notes) slides onto the others' (each on its own): {"rows":
        rows -> (rows slid, True where a leftover), "moved", "late" ticks, "slid" beats, "kslid" keys}, or the
        message saying why it can't."""
        # (the shapes' own notes as they sound, Sustain too: the very notes the moving one keeps, plain_copies own)
        stays = [n for j, n in enumerate(notes) if j != k and j not in skip]  # (skip: its own leftovers)
        if way in ("up", "down"):
            plan = up_down_plan(stays, notes[k], way == "up", self.keys - 1)
            if plan is None:
                return tr("join_split.merge_no_meet_" + way)
            if not plan["room"]:  # (out under / over a shape it overlaps: past key 0 / the top key, hunt)
                return tr("join_split.merge_no_room_" + way)
            return {"rows": lambda rows: up_down_rows(plan, rows), "moved": plan["moved"], "late": 0, "slid": 0.0,
                    "kslid": float(plan["slid"])}
        plan = side_plan(stays, notes[k], way == "right")
        if plan is None:
            return tr("join_split.merge_no_meet")
        by_key, least, late, room = plan
        if not room:
            return tr("join_split.merge_no_room_side")
        met =np.array(sorted(by_key), np.int64)
        moves = np.array([by_key[r] for r in met.tolist()], np.int64)

        def slid_rows(rows):
            at = np.clip(np.searchsorted(met, rows[:, 2]), 0, len(met) - 1)
            hit = met[at] == rows[:, 2]
            rows = rows.copy()
            rows[:, :2] += (np.where(hit, moves[at], least) + late)[:, None]
            return rows, ~hit
        return {"rows": slid_rows, "moved": bool(late or any(by_key.values())), "late": late,
                "slid": (least + late) / self.ppq, "kslid": 0.0}

    def put_merged(self, i, new, rest, stays, late, way, slid, kslid):
        """A two-shapes merge's fixed notes (new, from plain_copies) in place of shape i, remembering the shape it was
        for Split (merged again: the first one, hunt), its leftovers (rest) right after it; late ticks: the shapes
        that stay (and their Add between groups) move later too. All selected (the merge window goes on). One step."""
        sh = self.shapes[i]
        old = sh.get("unmerge")
        if old:  # (a Merged notes merged again: Split still gives back the shape it was at first, hunt)
            was = unmerged(sh)[0]
            got = box_map(old["box"], sh["pts"])
            if got is not None and np.allclose(got[0], np.eye(2), atol=1e-9):  # (only moved since: its slides add up)
                slid += old.get("slid", 0.0)
                kslid += old.get("kslid", 0.0)
            mid = old.get("id")
        else:
            was = copy.deepcopy(sh)
            was.pop("between", None)  # (its Add between group is unlinked)
            mid = None
        mid = mid or os.urandom(6).hex()
        new["name"] = tr("join_split.merged")
        new["unmerge"] = {"parts": [was], "box": [list(p) for p in new["pts"]],  # (moved / turned since: it too)
                          "slid": slid, "kslid": kslid, "way": way, "id": mid}
        if rest is not None:
            rest.update(name=tr("join_split.merge_leftovers"), made="leftovers", leftover_of=mid)
            ex = held_extract(sh)  # (like slice pieces: its loaded notes stay out until both are gone)
            if ex:
                rest["extract"] = ex
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.merge"))
        if late:  # (slid before the song's start: everything waits; an Add between group as a whole, hunt)
            stayed = [self.shapes[j] for j in stays]
            gids = {group_of(s) for s in stayed} - {None}
            for j, s in enumerate(self.shapes):
                if j != i and (any(s is t for t in stayed) or group_of(s) in gids):
                    s["pts"] = [[b + late / self.ppq, p] for b, p in s["pts"]]
        self.put_plain({i: new})
        if new.get("team"):  # (an Add between step: Split gives it back on its colour turn, like the notes, hunt)
            was["team"] = list(new["team"])
            if rest is not None:
                rest["team"] = list(new["team"])
        picked = stays + [i]
        if rest is not None:
            self.shapes.insert(i + 1, rest)
            picked = [j + (j > i) for j in picked] + [i + 1]
        self.select_many(picked, i)
        self.shapes_changed()

    def pieces(self):
        """The selected shapes that are pieces cut from another shape, keeping their notes (sliced.py)."""
        return [i for i in sorted(self.sels) if i < len(self.shapes) and self.shapes[i].get("cut")
                and not self.shapes[i]["cut"].get("mould")  # (cut with a shape: its own line would be the whole's)
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

    def plain_targets(self):
        """The selected shapes Turn into plain notes works on: not pasted notes already, pictures or masks (merged
        shapes yes: user, so they can be sliced)."""
        return [i for i in sorted(self.sels) if i < len(self.shapes) and (sh := self.shapes[i])
                and ("notes" not in sh or sh.get("merge")) and "picture" not in sh and not is_mask(sh)]

    def turn_into_plain_notes(self):
        """Right-click → Turn into plain notes (user, 2026-10-17: people sent shapes to Domino and back to freeze them,
        which moved their channels): each selected shape is replaced by a shape of its notes exactly as they sound
        now, each note on the channel it has now (like Apply mask, App.copied). One step."""
        self.commit_typing()
        got = self.plain_targets()
        if not got:
            return
        new = self.plain_copies(got)
        if not new:
            self.status.config(text=tr("join_split.plain_nothing"))
            return
        self.roll.cancel_draft()
        self.push_undo(name=tr("roll_menu.turn_into_plain_notes"))
        self.put_plain(new)
        self.select_many(got, self.sel if self.sel in got else got[0])
        self.shapes_changed()
        self.status.config(text=trn("join_split.plain_done", len(new), n=len(new)))

    def fill_targets(self):
        """The selected shapes Fill gaps works on: not Hz bass, images or masks (their notes are a preview)."""
        return [i for i in sorted(self.sels) if i < len(self.shapes) and (sh := self.shapes[i])
                and not sh.get("hz") and "picture" not in sh and not is_mask(sh)]

    def fill_problem(self, way, same):
        """Why Fill gaps would do nothing for the selection (the window's hint, Fill greyed), or None: the notes as
        Fill gaps takes them (each shape's own, laid as in Multi channel, hunt 10-25)."""
        got = self.fill_targets()
        key = (id(self.rendered), self.shapes_gen, self.channel_mode.get())
        kept = getattr(self, "_fill_multi", None)
        if kept and kept[0] == key:  # (outside Multi channel it's the whole project's notes again: once per change)
            multi = kept[1]
        else:
            multi = self.multi_rendered()[1]
            self._fill_multi = (key, multi)
        rows = multi[np.isin(multi[:, 5], got)] if got else multi[:0]
        if not len(rows):
            return tr("fill_gaps.nothing")
        return None if needs_fill(rows, way, same) else tr("fill_gaps.no_gaps")

    def fill_gaps(self, way, same):
        """Fill gaps (notes/fillgaps.py; window/fill_window.py): the selected shapes' notes, each shape's own as laid
        in Multi channel (Single channel merges same-start notes before the choice could count, hunt 10-25), every
        key row across all of them together, the gaps closed (way right / left / middle; same: which of the notes
        starting on one tick stays). Each shape becomes its own plain-notes shape, colours kept (fixed channels /
        image colours too); one that had notes and lost them all to another's same-start notes goes, its Extract
        handed to a shape that stays. One undo step."""
        self.commit_typing()
        got = self.fill_targets()
        if not got:
            self.status.config(text=tr("fill_gaps.nothing"))
            return
        changed, had = [], set()

        def together(rows):
            order = sorted(rows)
            had.update(order)
            both = np.concatenate([rows[i] for i in order])
            owner = np.concatenate([np.full(len(rows[i]), i, np.int64) for i in order])
            out, at = filled(both, way, same)
            changed.append(len(at) != len(both) or not np.array_equal(out, both[at]))
            owner = owner[at]
            o = np.argsort(owner, kind="stable")  # (each shape's part in one go: thousands of shapes, hunt)
            out, owner = out[o], owner[o]
            ends = np.searchsorted(owner, order + [order[-1] + 1])
            return {i: out[ends[j]:ends[j + 1]] for j, i in enumerate(order)}
        self.busy(tr("fill_gaps.busy"))
        try:
            new = self.plain_copies(got, together=together, whole=True)
        finally:
            self.busy(None)
        if not changed:  # (no notes at all)
            self.status.config(text=tr("fill_gaps.nothing"))
            return
        if not changed[0]:
            self.status.config(text=tr("fill_gaps.no_gaps"))
            return
        self.plain_kept(new, same == "both")
        gone = [i for i in sorted(had) if i not in new]  # (shapes that never had notes stay as they are, hunt)
        lost = clean_extract([g for i in gone for g in extract_groups(held_extract(self.shapes[i]))])
        if lost and new:  # (its loaded notes stay out of the file while the filled notes are there, hunt)
            heir = new[min(new)]
            heir["extract"] = clean_extract(extract_groups(heir.get("extract")) + extract_groups(lost))
        self.roll.cancel_draft()
        self.push_undo(name=tr("fill_gaps.undo"))
        self.put_plain(new)
        if gone:
            self.unlink_groups(gone)
            for i in reversed(gone):
                del self.shapes[i]
        kept = [i - sum(j < i for j in gone) for i in got if i not in gone]
        if kept:
            self.select_many(kept, kept[0])
        else:
            self.select_many([], None)
        self.shapes_changed()
        self.status.config(text=trn("fill_gaps.done", len(kept), n=len(kept)))

    def plain_kept(self, new, held=False):
        """Fill gaps / the line tools: what each new plain shape keeps of the shape it replaces (pasted / plain
        notes: their name; fixed channels, an image's colours, overlaps kept). held: overlaps kept for all."""
        for i, sh in new.items():
            old = self.shapes[i]
            if "notes" in old and not old.get("merge"):  # (pasted / plain notes already: keep their name)
                sh["name"] = old.get("name", sh["name"])
                if old.get("made"):
                    sh["made"] = old["made"]
            if old.get("own_slots") is True:  # (Extract / an image's masked notes: channels fixed in every mode,
                sh["own_slots"] = True       # hunt 10-25; without10 left its tracks + slots10 by that rule)
            if "picture_notes" in old:
                sh["picture_notes"] = copy.deepcopy(old["picture_notes"])
            if held or old.get("held"):  # (overlaps kept: not cut again by the overlap rule)
                sh["held"] = True

    def line_targets(self):
        """The selected shapes the line tools work on (Flip each line…; notes/lines.py): Fill gaps' (not Hz bass,
        images or masks). Drawn ones become plain notes in the same step (lines_window asks first)."""
        return self.fill_targets()

    def line_count(self, gap):
        """How many lines the line tools' shapes have (each shape's own colours = its tracks), or None: no notes."""
        got = self.line_targets()
        key = (id(self.rendered), self.shapes_gen, self.channel_mode.get())
        kept = getattr(self, "_fill_multi", None)
        if kept and kept[0] == key:
            multi = kept[1]
        else:
            multi = self.multi_rendered()[1]
            self._fill_multi = (key, multi)
        rows = multi[np.isin(multi[:, 5], got)] if got else multi[:0]
        if not len(rows):
            return None
        return note_lines(rows, line_tracks(rows[:, 5], rows[:, 4]), gap)[1]

    def flip_lines(self, sideways, gap, align=None, remove=False):
        """Flip each line sideways / upside down (notes/lines.py; user 2026-10-30, "shard flips"): the selected
        shapes' notes as laid in Multi channel (like Fill gaps), every line of each shape's colours mirrored inside its
        own time span / key range. align "up" / "down" (Line up the lines, user 2026-10-31): then every whole line
        moved onto the highest / lowest key of all the notes; remove: notes left overlapping another on their key
        go (else kept, never cut by the overlap rule). Each shape becomes its own plain-notes shape, colours kept.
        One undo step; nothing changing = status line, no step."""
        self.commit_typing()
        got = self.line_targets()
        if not got:
            self.status.config(text=tr("lines.nothing"))
            return
        flipped, removed, emptied = [], [], []

        def together(rows):
            order = sorted(rows)
            both = np.concatenate([rows[i] for i in order])
            owner = np.concatenate([np.full(len(rows[i]), i, np.int64) for i in order])
            line, n = note_lines(both, line_tracks(owner, both[:, 4]), gap)
            out = flipped_lines(both, line, n, sideways)
            if align:
                out = lined_up(out, line, n, align == "up")
            lose = overlap_losers(out, line) if align and remove else np.zeros(len(out), bool)
            flipped.append(len(np.unique(line[(out[:, :3] != both[:, :3]).any(1) | lose])))
            removed.append(int(lose.sum()))
            ends = np.searchsorted(owner, order + [order[-1] + 1])
            emptied.extend(i for j, i in enumerate(order) if lose[ends[j]:ends[j + 1]].all())
            return {i: out[ends[j]:ends[j + 1]][~lose[ends[j]:ends[j + 1]]] for j, i in enumerate(order)}
        self.busy(tr("lines.busy"))
        try:
            new = self.plain_copies(got, together=together, whole=True)
        finally:
            self.busy(None)
        if not flipped:
            self.status.config(text=tr("lines.nothing"))
            return
        if not flipped[0]:
            self.status.config(text=tr("lines.no_change"))
            return
        self.plain_kept(new, held=bool(align) and not remove)  # (lined up + overlaps kept: none cut by the rule)
        gone = sorted(emptied)
        self.roll.cancel_draft()
        self.push_undo(name=tr("lines.title_" + ("sideways" if sideways else "upside")))
        self.put_plain(new)
        if gone:  # (every note of a shape lost to overlaps: the shape goes, like Fill gaps)
            self.unlink_groups(gone)
            for i in reversed(gone):
                del self.shapes[i]
        kept = [i - sum(j < i for j in gone) for i in got if i not in gone]
        if kept:
            self.select_many(kept, kept[0] if self.sel in gone or self.sel not in got else
                             self.sel - sum(j < self.sel for j in gone))
        else:
            self.select_many([], None)
        self.shapes_changed()
        text = trn("lines.done", flipped[0], n=f"{flipped[0]:,}")
        if removed[0]:
            text += trn("lines.removed", removed[0], n=f"{removed[0]:,}")
        self.status.config(text=text)

    def split_lines(self, gap):
        """Split into pieces (notes/lines.py; user 2026-10-30): each line of each selected shape's colours onto a track
        of its own. Piece 1 stays on its colour's track (Spiderweb's automatic tracks made yours first, names kept:
        TrackList.keep_places); the next pieces go on new tracks right under it, same channel, each on the next port
        (A -> B-P), round the ports again from piece 17 (user 2026-10-30; status line says so), named "Name 2", "Name 3"... Pieces numbered like the lines (first start, lowest key).
        Each shape with pieces becomes its own plain-notes shape laid on those tracks. One undo step; nothing to split
        = status line, no step."""
        from window.app import own_chs, placed_on  # (app.py imports this file)
        self.commit_typing()
        got = self.line_targets()
        multi = self.multi_rendered()[1]
        rows = multi[np.isin(multi[:, 5], got)] if got else multi[:0]
        if not len(rows):
            self.status.config(text=tr("lines.nothing"))
            return
        groups = line_tracks(rows[:, 5], rows[:, 4])
        extra = note_lines(rows, groups, gap)[1] - len(np.unique(groups))
        if not extra:
            self.status.config(text=tr("lines.split_none"))
            return
        tl = self.track_list
        if len(tl.tracks()) + extra > COUNT_MOST:  # (a MIDI file's most tracks: refused before anything changes)
            messagebox.showwarning(tr("lines.title_split"), tr("lines.split_too_many", n=f"{len(tl.tracks()) + extra:,}",
                                                               most=f"{COUNT_MOST:,}"), parent=self)
            return
        self.roll.cancel_draft()
        self.push_undo(name=tr("lines.title_split"))
        pieces, ccs = {}, {}

        def together(rows):
            out = {}
            for i, x in rows.items():  # (each colour's lines in a row: note_lines numbers them by track first)
                line, n = note_lines(x, x[:, 4], gap)
                src = np.empty(n, np.int64)
                src[line] = x[:, 4]
                pieces[i] = (src, np.arange(n) - np.searchsorted(src, src, "left"))
                if has_cc(self.shapes[i]):  # (its CC events onto the pieces: the copy's own carrying goes by colour)
                    ccs[i] = split_cc(self.shapes[i], self.track_slots_in(x)(i), x, line, self.ppq)
                out[i] = np.column_stack([x[:, :4], line, x[:, 5:]])
            return out
        self.busy(tr("lines.split_busy"))
        try:
            chans = self.own_channels()
            if any(min(placed_on(self.shapes[i], chans), default=-1) < 0 for i in got):
                tl.keep_places(0)  # (piece 1 stays on its track, made yours like a track list drag: names stick)
                self.shapes_changed(now=True)
            new = self.plain_copies(got, together=together, whole=True)
        finally:
            self.busy(None)
        new = {i: sh for i, sh in new.items() if i in pieces and pieces[i][1].max(initial=0) > 0}
        for i, sh in new.items():
            if i in ccs:
                sh.pop("cc", None)
                if ccs[i]:
                    sh["cc"] = ccs[i]
        use10, chans, files = self.picture_use10, self.own_channels(), self.file_tracks()
        own = tl.named()
        by_id = {t["id"]: t for t in own}
        nid = new_id(own, self.own_next)
        under, intos, made, shared = {}, {}, 0, False
        for i in sorted(new):
            sh = self.shapes[i]
            have = placed_on(sh, chans)
            slots = self.laid_slots(sh) if have else []
            chs, homes = own_chs(sh) if have else ([], [])
            into, ich, ihome, kept = [], [], [], {}
            src, nth = pieces[i]
            for s, k in zip(src.tolist(), nth.tolist()):
                if k == 0:  # (piece 1: where the colour was; a track of Spiderweb's that couldn't be made yours: pinned)
                    v, slot = (have[s], slots[s]) if have else (-1 - s, s)
                    into.append(v)
                    ich.append(chs[s] if s < len(chs) else -1)
                    ihome.append(homes[s] if s < len(homes) else -1)
                    if v >= FILE0:
                        key, port = -1 - (v - FILE0), files.get(v - FILE0, {}).get("port", 0)
                        row = tl.row_of_id(key)
                        base = tl.name_of(row) if row is not None else ""
                    elif v >= 0:
                        key, port = ("own", v), by_id[v].get("port", 0) if v in by_id else 0
                        base = by_id[v]["name"] if v in by_id else ""
                    else:
                        key, port = -1 - v, 0
                        row = tl.row_of_id(-1 - v)  # (Single channel / As drawn: Multi channel's slot may not be a row)
                        base = tl.name_of(row) if row is not None else ""
                    kept[s] = (key, port, int(channels_of([slot], use10)[0]), base or tr("tracks.name", n=1))
                    continue
                key, port, ch, base = kept[s]
                shared = shared or k >= PORTS  # (round the ports again: shares port + channel with piece k - 15)
                t = with_port({"id": nid, "name": clean_name(tr("lines.piece_name", name=base, n=k + 1)),
                               "ch": ch, "after": -1}, (port + k) % PORTS)
                under.setdefault(key, []).append(t)
                into.append(nid)
                ich.append(-1)
                ihome.append(-1)
                nid += 1
                made += 1
            intos[i] = (into, ich, ihome)
        tl.tracks()
        laid = []
        for r in full_plan(self.file_rows(), tl._slots, own, tl._filled):
            laid.append(r)
            laid += under.get(("own", r["id"]) if isinstance(r, dict) else r, [])
        self.own_next = nid
        self.own_tracks = from_rows(laid, tl._slots, own + [t for ts in under.values() for t in ts], tl._filled)
        self.plain_kept(new)
        for i, sh in new.items():
            into, ich, ihome = intos[i]
            for k in ("own_slots", "slots10", "into_ch", "into_home", "into_rest", "into_gap", "cols", "into_new"):
                sh.pop(k, None)
            sh["into"] = into
            if any(c >= 0 for c in ich):  # (a colour on its own channel in its track stays so: piece 1)
                sh["into_ch"], sh["into_home"] = ich, ihome
            if any(v < 0 for v in into):
                sh["slots10"] = bool(use10)
        self.put_plain(new)
        self.select_many(got, self.sel if self.sel in got else got[0])
        tl.own_changed()
        self.status.config(text=trn("lines.split_done", made, n=f"{made:,}") +
                           (tr("lines.split_shared", most=PORTS) if shared else ""))

    def plain_copies(self, got, moved=None, own=False, together=None, whole=False):
        """Turn into plain notes' new shapes: {i: a shape of shape i's notes exactly as they sound now, each note on
        the channel it has now} for the shapes in got that have notes. moved(rows): their rendered rows changed
        first (Gate sensitive merge: slid). own: the shape's own notes, not as other shapes overlapping them cut
        them (Single channel; merge slides it away from them, hunt), each on its channel all the same.
        together({i: rows}) -> {i: rows}: every shape's rows changed at once (Fill gaps: key rows across shapes).
        whole: the notes as laid in Multi channel in every mode (Fill gaps: Single channel's merging left out)."""
        if self.notes_late:  # (notes still catching up with a drag: made now)
            self.shapes_changed(now=True)
        use10, multi = self.multi_rendered()
        shown = by_owner(multi if whole else self.rendered)  # (each shape's rows sliced out, not searched for:
        multi_of = shown if whole else by_owner(multi)       # thousands of shapes, hunt 10-25)
        new = {}
        auto = self.channel_mode.get() == "auto" or whole
        laid_rows = {}
        for i in got:
            rows = shown(i)
            sh = self.shapes[i]
            slots = rows if auto else multi_of(i)
            if own:
                n = np.asarray(self.notes_of(sh), np.int64).reshape(-1, 4)
                rows = np.column_stack([n, np.zeros((len(n), 1), np.int64), np.full((len(n), 1), i, np.int64)])
                slots = self.own_slots(i, use10)
            if len(rows):  # (a shape with no notes stays as it is)
                if self.in_own_track(sh):  # (laid on tracks: the copy is laid on them too, each note's colour =
                    rows = shown(i).copy()  # its track's place in the shape's list)
                    place = {s: k for k, s in reversed(list(enumerate(self.laid_slots(sh))))}
                    ids, back = np.unique(rows[:, 4], return_inverse=True)
                    rows[:, 4] = np.array([place.get(int(s), 0) for s in ids], np.int64)[back.ravel()]
                elif own or not auto:
                    rows = multi_slots(rows, slots)
                laid = self.track_slots_in(rows)  # (its CCs' tracks by these notes' slots, before any slide)
                laid_rows[i] = (rows, {i: laid(i)} if "cc" in sh else {})
        if together:
            changed = together({i: r for i, (r, _) in laid_rows.items()})
            laid_rows = {i: (changed[i], cc) for i, (_, cc) in laid_rows.items() if i in changed}
        for i, (rows, cc_slots) in laid_rows.items():
            sh = self.shapes[i]
            if moved:
                rows = moved(rows)
            if not len(rows):  # (none of them wanted: merge leftovers; Fill gaps: all lost to same-start notes)
                continue
            new[i] = without10(self.copied(rows, False, True, cc_slots.get), use10)
            new[i]["made"] = "plain"
            new[i]["name"] = tr("join_split.plain_name", name=self.shape_label(sh))
            if self.in_own_track(sh):
                new[i].pop("own_slots", None)
                new[i]["into"] = into_ids(sh)
                from notes.engine import pasted_notes
                if pasted_notes(sh):  # (a laid Domino paste's copy keeps its tracks in every mode, hunt 2026-10-29)
                    new[i]["from_paste"] = True
                if "slots10" in sh:
                    new[i]["slots10"] = sh["slots10"]
                else:
                    new[i].pop("slots10", None)
            ex = held_extract(sh) if moved is None else None  # (its notes taken from a loaded MIDI stay out of
            if ex:                                              # it, hunt 2026-10-25; a merge keeps its parts)
                new[i]["extract"] = ex
        return new

    def multi_rendered(self):
        """(slot numbering: channel 10 used, every note as in Multi channel): the shown notes in Multi channel, else
        worked out as if it were (each note's Multi channel slot kept too, for Multi channel later)."""
        if self.channel_mode.get() == "auto":
            return self.picture_use10, self.rendered
        from window.app import all_notes  # (app.py imports this file)
        # (the project's ticks as they are: all16 / in_order / rounds_apart are off outside Multi channel, hunt)
        all16 = self.use_ch10.get()
        _, use10, multi, _, _, _, _ = all_notes(self.shapes, self.ppq, self.keys, "auto", self.channel_split, all16,
                                       self.notes_tracks, rounds=self.new_rounds.get(),
                                       order=all16 and self.ch10_order.get(), keep=self.keep_colours.get(),
                                       busy=self.cc_busy("auto"), own=self.own_channels())
        return use10, multi

    def own_slots(self, i, use10):
        """Shape i's rendered rows in Multi channel as if alone (with its Add between group: its colour turn): its
        own colours, not moved off them by shapes overlapping it where it was (Gate sensitive merge slides it away
        from them; user 2026-10-19: a clash before the merge pinned it on the next round of channels). use10: the
        project's slot numbering (slot k = channel k)."""
        from window.app import all_notes  # (app.py imports this file)
        sh = self.shapes[i]
        gid = group_of(sh)
        alone = [s for s in self.shapes if s is sh or gid is not None and group_of(s) == gid]
        all16 = self.use_ch10.get()
        _, used10, rows, _, _, _, _ = all_notes(alone, self.ppq, self.keys, "auto", self.channel_split, all16, self.notes_tracks,
                                       rounds=self.new_rounds.get(), order=all16 and self.ch10_order.get(),
                                       busy=self.cc_busy("auto", alone), own=self.own_channels())
        rows = rows[rows[:, 5] == next(k for k, s in enumerate(alone) if s is sh)].copy()
        rows[:, 5] = i
        if use10 and not used10 and not self.in_own_track(sh):  # (made with channel 10 skipped: the same channels in the project's numbering)
            t = rows[:, 4]
            rows[:, 4] = t // 15 * 16 + np.asarray(SKIP10)[t % 15]
        return rows

    def put_plain(self, new):
        """plain_copies' shapes in place of the ones they were made of (no undo step: the caller's)."""
        laps = []  # (an Add between group: unlinked, user; each shape keeps its colour turn and its repeat, so with
        turns = between_turns(self.shapes, laps)  # "Repeated colours get new channels" it stays on its channels, hunt)
        gids = {group_of(self.shapes[i]) for i in new} - {None}
        for i, sh in enumerate(self.shapes):
            if group_of(sh) in gids and turns[i] is not None:
                sh["team"] = list(turns[i]) + ([laps[i]] if laps[i] is not None else [])
        for i in new:
            if turns[i] is not None:
                new[i]["team"] = list(turns[i])
        self.unlink_groups(sorted(new))
        for i, sh in new.items():
            self.shapes[i] = sh

    def can_split_pieces(self, sh):
        """A joined curve with more than one piece / shape in it, a live shape that can go back to the shapes it was
        made of, a Gate sensitive merge (either way), or a custom drawing with separate parts."""
        if sh["kind"] == "custom" and (originals(sh) or merge_parts(sh)):
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
        for p in parts if velocity else ():  # (pieces of it, not the shapes it was made of back)
            piece_velocity(p, old, span(p), whole, lead="cut" in p)
            part_glue(p, old)
            keep_velocity(p)
            keep_was(p, old, span(p), whole)
        if velocity:  # (like Slice, hunt: Sustain's stops put by hand kept per piece, Gate subtract's graph cut)
            carry_stops(self, [old], parts)
            carry_subtract(self, old, parts)
            carry_extend(self, old, parts)  # (Gate extend's graph too)
            carry_recolour(self, old, parts)
        self.shapes[i:i + 1] = parts
        self.select_many(range(i, i + len(parts)), i)
        self.shapes_changed()

    def split_pieces(self, i):
        sh = self.shapes[i]
        if not self.can_split_pieces(sh):
            return
        if sh.get("merge"):  # Gate sensitive merge: the two shapes as they were before it, moved along with it
            m = sh["merge"]
            got = reshaped_parts(sh, split=True)
            if got:  # (turned / slanted / stretched: the parts that way, the sliding one where it slid)
                back = got[0]
            else:  # (moved, or a part that can't follow a flip / turn: as far as the box's lowest, earliest corner, hunt)
                db, dp = ((low_corner(sh["pts"]) - low_corner(m["box"])).tolist() if m.get("box") else
                          (sh["pts"][0][0] - m["at"][0], sh["pts"][0][1] - m["at"][1]) if m.get("at") else (0, 0))
                back = copy.deepcopy(m["parts"])
                for p in back:
                    p["pts"] = [[b + db, q + dp] for b, q in p["pts"]]
            if not sh.get("own_vel"):  # (its velocity was changed: each shape takes the line where its notes sat)
                moves = [(0.0, 0.0)] * 2 if got else edge_moves(m)
                line = sh
                if sh.get("gate_flip"):  # (its line as before its gate aware flip)
                    line = copy.deepcopy(sh)
                    turn_velocities(line)
                for p, (d0, d1) in zip(back, moves):
                    lo, hi = span(p)
                    piece_velocity(p, line, (lo + d0, hi + d1), span(sh))
                    p.pop("own_vel", None)
            put_back(back, m.get("flips"))  # (gate flipped when merged: again, user: as before merging)
            self.replace_shape(i, back, velocity=False, name=tr("join_split.split_back_into_the_old_shapes"))
            self.status.config(text=tr("join_split.back_to_the_shapes_it_was", n=2))
            return
        if sh.get("unmerge"):  # merged keeping shapes separate: the shape its notes were, moved along with them
            self.replace_shape(i, unmerged(sh), velocity=False, name=tr("join_split.split_back_into_the_old_shapes"))
            mid = sh["unmerge"].get("id")
            # (its "Merge leftovers" while still there, every merge's: back in it too, user 2026-10-19, hunt)
            lone = [j for j, s in enumerate(self.shapes) if mid and s.get("leftover_of") == mid]
            if lone:
                self.unlink_groups(lone)
                for j in reversed(lone):
                    del self.shapes[j]
                self.select(i - sum(j < i for j in lone))
                self.shapes_changed()
            self.status.config(text=tr("join_split.back_to_the_shape_it_was"))
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
        baked = has_formula(self.shapes[i])
        self.replace_shape(i, list(got), name=tr("join_split.split_here"))
        self.status.config(text=tr("join_split.split_in_two_baked" if baked else "join_split.split_in_two"))
        self.tips.show("join", wait=True)

    def cut_at(self, sh, at, mark=None, exact=False):
        """A line kind cut in two at the spot at (x, y on screen) -> its two halves, or None (too near an end).
        They're pieces that keep their notes (sliced.py); mark: the cut's mark ({"kind": "slice", "dir": ...}; default
        Split here's scissors). exact (Slice / Lasso: where their line crosses it): never moved onto a corner near it
        (a right-click is)."""
        got = self._cut_at(sh, at, exact)
        if got:
            roll = self.roll
            scale = (abs(roll.t2x(1) - roll.t2x(0)), abs(roll.p2y(0) - roll.p2y(1)))
            cut_in_two(sh, got, got[0]["pts"][-1], scale, mark or {"kind": "split"})
        return got

    def _cut_at(self, sh, at, exact=False):
        roll = self.roll
        if has_formula(sh):  # (each half would lay the whole formula again: made into a plain curve first, as Split
            sh = copy.deepcopy(sh)  # does; the halves still make the whole's notes)
            bake_shape(sh)
        if sh["kind"] == "curve":
            c = copy.deepcopy(sh)
            seg, t, _ = nearest(sh["pts"], roll.to_xy, at.x, at.y, gaps=sh.get("gaps", ()))
            a = self.anchor_near(sh["pts"], seg, at, exact)
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
            got = self.split_line(sh, at, exact)
        return list(got) if got else None

    def slice_along(self, a, b):
        """The Slice tool's line from a to b (beat, key) let go: every line kind it crosses cut there, every custom
        shape it goes all the way across cut in two (slice.py); a shape with note tool pages / glue: its notes, where
        it goes all the way across them (sliced.knife_in_two). With Select boxes kept: only the shapes they
        selected, and only inside the boxes. One undo step; the pieces end up selected."""
        roll = self.roll
        boxes = roll.kept_box()
        cuts = [(a, b, None)] if not boxes else [(*s, box) for s, box in ((clip_segment(a, b, box), box)
                                                                           for box in boxes) if s]
        segs = [(sa, sb) for sa, sb, _ in cuts]
        targets = sorted(self.sels) if boxes else self.shown_shapes()
        done, out, skipped, plain, missed, merges, flat = {}, {}, 0, set(), 0, 0, False
        for i in targets:
            sh = self.shapes[i]
            if self.lasso_pasted(sh):
                # pasted notes (not masks: never cut, hunt 10-23): by time, each row the line crosses (slice.slice_notes); pieces = new pasted notes
                pieces, crossed = [sh], False
                for sa, sb, box in cuts:
                    nxt = []
                    for p in pieces:
                        got, hit = slice_notes(p, *self.all_rows(p), sa, sb, self.ppq, box, self.keys)
                        crossed |= hit
                        nxt += got or [p]
                    pieces = nxt
                plain.add(i)
                if len(pieces) == 1 and crossed:
                    missed += 1
                    flat |= any(abs(sb[1] - sa[1]) < EPS for sa, sb in segs)
            elif sh["kind"] in ("custom", "funnel") and ("notes" in sh or sh.get("text") or sh.get("hz")):
                if sh.get("merge") and any(any(notes_across(self.notes_of(sh), sa, sb, self.ppq)) for sa, sb in segs):
                    merges += 1  # (touched: say how to cut it, user 2026-10-17)
                continue
            elif (sh["kind"] == "custom" or sh["kind"] in LINE_KINDS) and tooled(sh) or sh["kind"] == "funnel":
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
                        got = self.cut_at(pieces[k], at, {"kind": "slice", "dir": [sb[0] - sa[0], sb[1] - sa[1]]},
                                          exact=True)
                        if got:
                            pieces[k:k + 1] = got
            else:
                continue
            if len(pieces) > 1:
                done[i] = pieces
        if not done:
            self.status.config(text=tr("join_split.slice_merged") if merges else
                               tr("join_split.slice_partly") if skipped else
                               tr("join_split.slice_flat_missed") if flat else
                               tr("join_split.slice_missed_notes") if missed else tr("join_split.slice_nothing"))
            return
        picked = self.put_pieces(done, plain, tr("join_split.slice"))
        self.select_many(picked, picked[0])
        self.shapes_changed()
        n = sum(len(p) for p in done.values())
        self.status.config(text=tr("join_split.sliced", shapes=trn("n.shapes", len(done), n=len(done)),
                                   pieces=trn("n.pieces", n, n=n)) +
                           (trn("join_split.slice_partly_too", skipped, n=skipped) if skipped else "") +
                           (trn("join_split.slice_merged_too", merges, n=merges) if merges else ""))

    def lasso_cut(self, loop):
        """The Lasso tool's closed loop (beat, key points) cut: every pasted-notes shape it takes notes in is cut in
        two along it (notes/lasso.py), each note across it cut there; the inside pieces end up selected, one undo
        step. With Select boxes kept: only the shapes they selected, only inside the boxes. Any other shape it
        takes notes in (drawn shapes, text, merged, Hz bass; not pictures / masks): asked once, then turned into
        plain notes and cut in the same undo step (user 2026-10-23: a lasso doing nothing surprised more). A shape
        wholly inside: not cut, selected. -> True when done, False when there was nothing to cut, None when the
        question was answered No (the loop stays)."""
        self.commit_typing()
        if self.notes_late:  # (notes still catching up with a drag: made now)
            self.shapes_changed(now=True)
        boxes = self.roll.kept_box() or None
        targets = sorted(self.sels) if boxes else self.shown_shapes()
        drawn, whole = [], []  # (a drawn shape wholly inside: just selected, not asked about; hunt 10-23)
        for i in targets:
            if self.lasso_drawn(self.shapes[i]):
                got = takes(self.notes_of(self.shapes[i]), loop, self.ppq, boxes)
                if got:
                    (drawn if got == "cut" else whole).append(i)
        if drawn:
            if not messagebox.askokcancel(tr("join_split.lasso"), trn("join_split.lasso_ask_plain", len(drawn),
                                                                     n=len(drawn)), icon="question", parent=self):
                return None
            new = self.plain_copies(drawn)
        done = {}
        for i in targets:
            sh = new[i] if drawn and i in new else self.shapes[i]
            if self.lasso_line(sh):  # (lines: cut where it crosses them, staying lines, like Slice; E2)
                got = self.lasso_line_pieces(sh, loop, boxes)
                if got is True:
                    whole.append(i)
                elif got:
                    done[i] = got
                continue
            if not self.lasso_pasted(sh):
                continue
            notes, tracks = self.all_rows(sh)
            got, _ = lasso_notes(sh, notes, tracks, loop, self.ppq, boxes)
            if got:
                got[1]["_inside"] = True
                done[i] = got
            elif len(notes) and touches(notes, loop, self.ppq, boxes):
                whole.append(i)  # (all of it inside)
        if not done and not drawn:
            if whole:
                self.select_many(whole, whole[0])
                self.status.config(text=trn("join_split.lasso_whole", len(whole), n=len(whole)))
            else:
                self.status.config(text=tr("join_split.lasso_nothing"))
            return False
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.lasso"))
        if drawn:
            self.put_plain(new)
        inside = [self.shapes[i] for i in whole]
        pasted = {i for i in done if not self.lasso_line(self.shapes[i])}  # (lines' pieces get their velocities)
        picked = self.put_pieces(done, pasted, None, pick=lambda p: p.pop("_inside", False)) if done else []
        for p in (p for parts in done.values() for p in parts):
            p.pop("_inside", None)
        picked += [k for k, s in enumerate(self.shapes) if any(s is w for w in inside)]  # (wholly inside ones too)
        self.select_many(picked, picked[0] if picked else None)
        self.shapes_changed()
        n = len(done)
        self.status.config(text=trn("join_split.lasso_cut", n, n=n) if done else
                           trn("join_split.lasso_whole", len(whole), n=len(whole)))
        return True

    def can_knife(self, sh):
        """A shape Slice with shape cuts with (user 2026-10-24): custom shapes, pasted notes, text; not lines,
        pictures, masks, merged shapes, Hz bass."""
        return sh["kind"] == "custom" and "picture" not in sh and "merge" not in sh and not sh.get("hz") \
            and not is_mask(sh)

    def knife_menu(self):
        """Right-click Slice with shape (user 2026-10-26): the front-most selected shape is the knife; one selected =
        it cuts the shapes behind it, several = the other selected ones (unselected stay whole). -> (knife, targets)
        when its notes are over one of them, else None (no item)."""
        sels = sorted(self.sels)
        if not sels or sels[-1] in self.hidden_shapes or not self.can_knife(self.shapes[sels[-1]]):
            return None
        k = sels[-1]
        among = sels[:-1] if len(sels) > 1 else range(k)
        among = [i for i in among if i not in self.hidden_shapes and not is_mask(self.shapes[i])
                 and "picture" not in self.shapes[i]]
        knife = np.asarray(self.knife_notes(k)) if among else ()
        if not len(knife):
            return None
        t0, t1, k0, k1 = knife[:, 0].min(), knife[:, 1].max(), knife[:, 2].min(), knife[:, 2].max()
        spans = None
        for i in among:
            sh = self.shapes[i]
            if "merge" in sh or sh.get("hz"):  # (can't be cut: no item for them alone, hunt 10-26)
                continue
            ns = np.asarray(self.notes_of(sh))
            if not len(ns) or ns[:, 0].min() >= t1 or ns[:, 1].max() <= t0 or ns[:, 2].min() > k1 \
                    or ns[:, 2].max() < k0:  # (quick: nowhere near the knife)
                continue
            spans = knife_spans(knife) if spans is None else spans
            if takes(ns, spans, self.ppq):
                return k, among
        return None

    def knife_notes(self, k):
        """Shape k's notes that cut: with a track highlighted in the track list only those on the shown tracks
        (hunt 10-26: hidden ones cut out of sight)."""
        notes = self.notes_of(self.shapes[k])
        if self.track_list.current is None or getattr(self, "drawn_all", None) is None:
            return notes
        d = self.drawn_all
        return d[(d[:, 5] == k) & np.isin(self.drawn_slots, list(self.track_list.shown_slots()))][:, :4]

    def knife_cut(self, k, only=None):
        """Slice with shape: shape k's own notes cut every shown shape under them (notes/lasso.knife_spans: on each
        key row the stretches its notes cover, touching ones joined; no notes = no cut, even inside a ring). The
        part under the knife is the inside piece, every note across an edge cut in two there; the knife stays.
        Drawn shapes, lines and text under it: asked once, then turned into plain notes and cut in the same undo
        step (user 2026-10-26, like the Lasso); merged shapes / Hz bass stay whole (Slice's message). Pieces keep
        their colours; the inside pieces (and shapes wholly under it) end up selected. only: cut just these (the
        right-click's, knife_menu). -> True when cut."""
        self.commit_typing()
        if self.notes_late:  # (notes still catching up with a drag: made now)
            self.shapes_changed(now=True)
        knife = self.shapes[k]
        name = self.shape_label(knife)
        spans = knife_spans(self.knife_notes(k))
        if not spans:
            self.status.config(text=tr("join_split.knife_no_notes", name=name))
            return False
        drawn, whole, skipped = [], [], 0
        targets = [i for i in self.shown_shapes() if i != k and not is_mask(self.shapes[i])
                   and "picture" not in self.shapes[i] and (only is None or i in only)]
        for i in targets:
            sh = self.shapes[i]
            if self.lasso_pasted(sh):
                continue
            got = takes(self.notes_of(sh), spans, self.ppq)
            if not got:
                continue
            if "merge" in sh or sh.get("hz"):
                skipped += 1
            elif got == "whole":
                whole.append(i)
            else:
                drawn.append(i)
        if drawn:
            if not messagebox.askokcancel(tr("join_split.knife"), trn("join_split.knife_ask_plain", len(drawn),
                                                                     n=len(drawn), name=name),
                                          icon="question", parent=self):
                return False
            new = self.plain_copies(drawn)
        done = {}
        for i in targets:
            sh = new[i] if drawn and i in new else self.shapes[i]
            if not self.lasso_pasted(sh):
                continue
            notes, tracks = self.all_rows(sh)
            got, _ = lasso_notes(sh, notes, tracks, spans, self.ppq)
            if got:
                got[1]["_inside"] = True
                done[i] = got
            elif len(notes) and touches(notes, spans, self.ppq):
                whole.append(i)
        more = trn("join_split.knife_skipped", skipped, n=skipped) if skipped else ""
        if not done and not drawn:
            if whole:
                self.select_many(whole, whole[0])
                self.status.config(text=trn("join_split.knife_whole", len(whole), n=len(whole), name=name) + more)
            else:
                self.status.config(text=tr("join_split.knife_only_skipped") if skipped else
                                   tr("join_split.knife_nothing", name=name))
            return False
        self.roll.cancel_draft()
        self.push_undo(name=tr("join_split.knife"))
        if drawn:
            self.put_plain(new)
        inside = [self.shapes[i] for i in whole]
        picked = self.put_pieces(done, set(done), None, pick=lambda p: p.pop("_inside", False)) if done else []
        for p in (p for parts in done.values() for p in parts):
            p.pop("_inside", None)
        picked += [j for j, s in enumerate(self.shapes) if any(s is w for w in inside)]
        self.select_many(picked, picked[0] if picked else None)
        self.shapes_changed()
        n = len(done)
        self.status.config(text=(trn("join_split.knife_cut", n, n=n, name=name) if done else
                                 trn("join_split.knife_whole", len(whole), n=len(whole), name=name)) + more)
        return True

    def new_colour_targets(self):
        """The selected plain notes shapes New colours ▸ works on (pasted / plain notes, pieces; not pictures,
        masks, merged shapes; not shapes laid in a track: the track's channel wins, hunt 10-26)."""
        return [i for i in sorted(self.sels) if self.lasso_pasted(self.shapes[i]) and not self.shapes[i].get("into")]

    def free_slots(self, i, n, given=(), apart=()):
        """n slots (render's) no other shape's note uses while shape i sounds (first start .. last end), lowest
        first, within the first 15 / 16 (more = colours sounding already: None); channel 10 only with Use channel
        10 (app.all16), and last (hunt 10-26). given: (t0, t1, slots) handed out already this go; apart: shapes
        getting new colours too (their old slots count as free)."""
        r = self.rendered
        mine = r[r[:, 5] == i]
        if not len(mine):
            return list(range(n))
        t0, t1 = int(mine[:, 0].min()), int(mine[:, 1].max())
        near = r[~np.isin(r[:, 5], [i, *apart]) & (r[:, 0] < t1) & (r[:, 1] > t0)]
        block = 16 if self.picture_use10 else 15
        used = set((near[:, 4] % block).tolist())  # (by channel: a later block's slot sounds on the same one)
        for a, b, slots in given:
            if a < t1 and b > t0:
                used.update(slots)
        order = [s for s in range(block) if not (self.picture_use10 and s == 9)]
        if self.all16():
            order.append(9)
        out = [s for s in order if s not in used][:n]
        return out if len(out) == n else None

    def new_colours(self, ch):
        """New colours ▸ (user 2026-10-24, Slice with shape pieces keep their colours until this): ch None = Next
        free colours = channels no other shape uses while it sounds (my pick 10-26: by the usual overlap rules
        pieces cut side by side never clash on a key, so nothing changed), each of its colours one, pinned; ch 1-16
        = every note on that channel, pinned. Pinned = own_slots True (any Channels mode, like a picture's). One
        undo step."""
        got = self.new_colour_targets()
        if not got:
            return
        self.commit_typing()
        if self.notes_late:
            self.shapes_changed(now=True)
        use10 = bool(self.picture_use10)
        plans, given = {}, []
        for i in got:
            rows = unpack_notes(self.shapes[i]["notes"])
            olds = np.unique(rows[:, 4])
            if ch is None:  # (rendered slots: slots10 = the slot rule they were made under; one shape after another,
                # so shapes picked together get colours apart too, hunt 10-26)
                free = self.free_slots(i, len(olds), given, got)
                if free is None:
                    self.status.config(text=tr("join_split.new_none"))
                    return
                mine = self.rendered[self.rendered[:, 5] == i]
                if len(mine):
                    given.append((int(mine[:, 0].min()), int(mine[:, 1].max()), free))
                plans[i] = (dict(zip(olds.tolist(), free)), use10)
            else:  # (slots10 True: slot k = channel k; else channel 10 skipped)
                plans[i] = ({t: 9 if ch == 10 else SKIP10.index(ch - 1) for t in olds.tolist()}, ch == 10)
        self.push_undo(name=tr("join_split.new_colours"))
        for i, (to, rule) in plans.items():
            sh = self.shapes[i]
            sh["paste"] = uuid.uuid4().hex[:12]  # (no longer shares channels with the other pieces of its paste)
            rows = unpack_notes(sh["notes"]).copy()
            rows[:, 4] = [to[t] for t in rows[:, 4].tolist()]
            sh["notes"] = pack_notes(rows)
            if "cc" in sh:
                sh["cc"] = pack_cc([(e[0], to.get(e[1], e[1]), e[2], e[3]) for e in unpack_cc(sh["cc"])])
            sh["own_slots"], sh["slots10"] = True, rule
            if ch is not None:
                sh.pop("strips", None)  # (one channel: Channel per strip has nothing left to do)
        self.shapes_changed()
        self.status.config(text=trn("join_split.new_free_done", len(got), n=len(got)) if ch is None else
                           trn("join_split.new_channel_done", len(got), n=len(got), ch=ch))

    def reverse_targets(self):
        """The New colours ▸ shapes Reverse track order can turn round: 2+ colours, no Channel per strip (its
        strips pick the channels, the colours wouldn't count), colours that play apart in this Channels mode
        (app.colours_apart: else nothing changes until Multi channel, hunt 11-06), and no Colours Shuffle /
        Scattered / Clumps (it picks the shown colours again from the same list: the reverse barely showed)."""
        return [i for i in self.new_colour_targets() if self.reverse_why(i) is None]

    def reverse_why(self, i):
        """Why shape i can't be reversed: None (it can), "colours" (Colours Shuffle / Split on: the menu says to
        turn it off first) or "no" (one colour, Channel per strip, colours playing together in this mode)."""
        from window.app import colours_apart
        sh = self.shapes[i]
        if "strips" in sh or len(np.unique(unpack_notes(sh["notes"])[:, 4] % ROUND)) < 2 \
                or not colours_apart(sh, self.channel_mode.get()):
            return "no"
        if is_recoloured(sh) and sh["recolour"].get("mode") != KEEP:
            return "colours"
        return None

    def reverse_tracks(self):
        """Reverse track order (tester 11-04 + user 11-05 picked A: the cut piece's right-click, beside New
        colours ▸; any plain notes): each selected shape's colours turned round, its first <-> its last (like the
        Stir area's "reverse", on the whole shape). Same colours, same paste (it stays on the tracks it had, in
        the other order); CC passengers stay with their tracks. One undo step."""
        got = self.reverse_targets()
        if not got:
            return
        self.commit_typing()
        if self.notes_late:
            self.shapes_changed(now=True)
        self.push_undo(name=tr("join_split.reverse_tracks"))
        for i in got:
            sh = self.shapes[i]
            rows = unpack_notes(sh["notes"]).copy()
            low = rows[:, 4] % ROUND
            lap = rows[:, 4] - low  # (each note's round stays)
            if is_recoloured(sh):  # (a Stir: each colour set turned round on its own, like the Stir's "reverse";
                # across sets colours moved into another set, hunt 11-06)
                which, owns = colour_sets(low)
                new = low.copy()
                for s, own in enumerate(owns):
                    m = which == s
                    new[m] = own[::-1][np.searchsorted(own, low[m])]
                # (and the Stir told: it stirs from the colours before and swaps its result the same way, so the
                # pattern stays exactly, tracks turned round; stirring the swapped colours made another one, sweep
                # 11-06: even an extra track)
                swap = {int(a): int(b) for own in owns for a, b in zip(own.tolist(), own[::-1].tolist())}
                rc = dict(sh["recolour"])
                tw = turn_again(rc.pop("turned", None), swap)
                sh["recolour"] = dict(rc, turned=tw) if tw else rc
            else:
                olds = np.unique(low)
                new = olds[::-1][np.searchsorted(olds, low)]
            rows[:, 4] = lap + new
            sh["notes"] = pack_notes(rows)
        self.shapes_changed()
        self.status.config(text=trn("join_split.reverse_done", len(got), n=len(got)))

    def lasso_magnet_notes(self):
        """The notes a Magnetic lasso clings to: those it can cut as they are (pasted notes, lines; with Select boxes
        kept: only what they selected), (start, end, key) ticks."""
        targets = sorted(self.sels) if self.roll.kept_box() else self.shown_shapes()
        got = [np.asarray(self.notes_of(self.shapes[i]))[:, :3] for i in targets
               if self.lasso_pasted(self.shapes[i]) or self.lasso_line(self.shapes[i])]
        got = [n for n in got if len(n)]
        return np.concatenate(got).astype(np.int64) if got else np.zeros((0, 3), np.int64)

    def lasso_pasted(self, sh):
        """Pasted notes the lasso (and Slice) cut as they are: not masks (never cut, hunt 10-23)."""
        return (sh["kind"] == "custom" and "notes" in sh and "picture" not in sh and "merge" not in sh
                and not sh.get("text") and not sh.get("hz") and not is_mask(sh))

    def lasso_line(self, sh):
        """A line the lasso cuts as a line (Slice's way): line kinds without note tool pages / glue; not masks."""
        return sh["kind"] in LINE_KINDS and not tooled(sh) and not is_mask(sh)

    def lasso_drawn(self, sh):
        """A shape the lasso turns into plain notes before cutting it (Turn into plain notes' kinds)."""
        return not self.lasso_pasted(sh) and not self.lasso_line(sh) and ("notes" not in sh or sh.get("merge")) \
            and "picture" not in sh and not is_mask(sh)

    def lasso_line_pieces(self, sh, loop, boxes):
        """A line cut where the loop crosses it (inside the kept boxes): its pieces, those inside the loop marked
        "_inside"; True = wholly inside (not cut); None = the loop doesn't take it in."""
        roll = self.roll
        path = np.asarray(cached_path(sh), float).reshape(-1, 2)

        def boxed(pt):
            return boxes is None or any(min(b[0], b[2]) <= pt[0] <= max(b[0], b[2])
                                        and min(b[1], b[3]) <= pt[1] <= max(b[1], b[3]) for b in boxes)
        hits = [(pt, way) for pt, way in loop_crossings(path, loop) if boxed(pt)]
        for b in boxes or ():  # (the kept boxes' edges inside the loop cut it too, like the notes; hunt 10-23)
            b0, b1, p0, p1 = min(b[0], b[2]), max(b[0], b[2]), min(b[1], b[3]), max(b[1], b[3])
            for a, c in (([b0, p0], [b1, p0]), ([b1, p0], [b1, p1]), ([b1, p1], [b0, p1]), ([b0, p1], [b0, p0])):
                hits += [(pt, [c[0] - a[0], c[1] - a[1]]) for pt, _ in crossings(path, a, c)
                         if inside_point(loop, pt)]
        if not hits:
            mid = path_middle(path)
            return True if inside_point(loop, mid) and boxed(mid) else None
        pieces = [sh]
        for pt, way in hits:
            at = SimpleNamespace(x=roll.t2x(pt[0]), y=roll.p2y(pt[1]), state=0)
            k = min(range(len(pieces)), key=lambda j: self.path_dist(pieces[j], at))  # (the piece it's on)
            got = self.cut_at(pieces[k], at, {"kind": "slice", "dir": way}, exact=True)  # (its mark along the loop)
            if got:
                pieces[k:k + 1] = got
        if len(pieces) == 1:  # (only too near an end to cut)
            mid = path_middle(path)
            return True if inside_point(loop, mid) and boxed(mid) else None
        for p in pieces:
            mid = path_middle(np.asarray(cached_path(p), float).reshape(-1, 2))
            if inside_point(loop, mid) and boxed(mid):
                p["_inside"] = True
        closed = len(path) > 2 and np.hypot(*(path[0] - path[-1])) < 1e-9
        if closed and len(pieces) >= 3 and bool(pieces[0].get("_inside")) == bool(pieces[-1].get("_inside")):
            # (a closed line's last and first pieces meet at its start: one piece, hunt 10-23)
            side = bool(pieces[0].get("_inside"))
            got = self.joined_shape([{k: v for k, v in q.items() if k != "_inside"} for q in (pieces[-1], pieces[0])])
            if got is not None:
                if side:
                    got["_inside"] = True
                pieces = [got] + pieces[1:-1]
        return pieces if any(p.get("_inside") for p in pieces) else None

    def put_pieces(self, done, plain, name, pick=None):
        """Cut shapes replaced by their pieces (done: shape number -> pieces; plain: pasted notes, kept as they
        are), one undo step (name None: the caller's step) -> the pieces' new numbers (pick(piece): only those it says
        yes to)."""
        self.roll.cancel_draft()
        if name is not None:
            self.push_undo(name=name)
        self.unlink_groups(done)
        for parts in done.values():
            for p in parts:
                p.pop("between", None)
        new, picked = [], []
        for i, sh in enumerate(self.shapes):
            parts = done.get(i, [sh])
            whole = span(sh)
            for p in parts if i in done and i not in plain else ():  # (pasted notes: their notes kept as they were)
                piece_velocity(p, sh, span(p), whole, lead="cut" in p)
                part_glue(p, sh)
                keep_velocity(p)
                keep_was(p, sh, span(p), whole)
            if i in done:
                carry_stops(self, [sh], parts)  # (Sustain's stops put by hand: each piece keeps the ones inside it)
                carry_subtract(self, sh, parts)  # (Gate subtract's graph across the shape: cut per piece)
                carry_extend(self, sh, parts)  # (Gate extend's graph too)
                if i not in plain:
                    carry_recolour(self, sh, parts)  # (each piece keeps the colours it had in the whole)
                picked += [len(new) + k for k, p in enumerate(parts) if pick is None or pick(p)]
            new += parts
        self.shapes[:] = new
        return picked

    def all_rows(self, sh):
        """A pasted-notes shape's notes and tracks for the Slice / Lasso tools, rows moved past the top key too (they're
        hidden but kept: a slice mustn't lose them). As they sound, Sustain / Gate subtract done: the pieces sound
        exactly as before and don't keep those settings (user 2026-10-23: a cut turns them into plain notes;
        slice._halves)."""
        (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
        if max(p0, p1, p2, p1 + p2 - p0) <= self.keys - 0.5:
            return self.notes_tracks(sh)
        return shape_notes_tracks(sh, self.ppq, 256)

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

    def anchor_near(self, pts, seg, at, exact=False):
        """The segment's anchor the right-click was on (near; exact: right on it), or None."""
        d, a = min((math.hypot(self.roll.to_xy(pts[3 * a])[0] - at.x, self.roll.to_xy(pts[3 * a])[1] - at.y), a)
                   for a in (seg, seg + 1))
        return a if d <= (ON_PX if exact else TOUCH_PX * self.scale) else None

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

    def split_line(self, sh, at, exact=False):
        """A line / polyline / freehand stroke cut in two: at the point right-clicked on, or on the nearest part of
        it (a freehand stroke: at its nearest drawn point; a straightened one is cut from the straightened line, so
        the halves look the same, and they're no longer straightened)."""
        pts = sh["pts"]
        if sh["kind"] == "free" and sh.get("smooth"):
            pts = [list(p) for p in smooth_path([tuple(p) for p in pts], sh["smooth"], sh.get("k", 1.0))]
            sh = {key: v for key, v in sh.items() if key != "smooth"}
        j, u, seg_px = self.nearest_on(pts, at)
        a, b = pts[j], pts[j + 1]
        near = ON_PX if exact else TOUCH_PX * self.scale
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
