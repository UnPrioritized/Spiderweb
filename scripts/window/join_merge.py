"""The Gate sensitive merge part of Join / Split (a mixin of App; moved out of join_split.py)."""

import copy
import os
import numpy as np
from files.lang import tr, trn
from notes.between import group_of
from notes.custom import notes_shape
from notes.engine import SHAPE_DEFAULTS, clean_shape
from notes.gateflip import lifted
from notes.loaded import clean_extract, extract_groups, held_extract
from notes.mask import is_mask, same_mask
from notes.sustain import alike
from notes.merge import LAP, box_map, merged, merged_up_down, mover, part_rows, side_plan, spans_of, unmerged, up_down_plan, up_down_rows
from notes.subtract import plain_subtract, same_subtract
from window.sustain_window import carry_stops


class JoinMerge:
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
        rows = part_rows(parts, self.ppq, self.keys, self.rounds_apart(), self.keeps_offrange())
        if not all(len(r) for r in rows):  # (e.g. a shape wholly past the top key: its notes aren't made)
            return tr("join_split.merge_no_notes")
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
        # ("Keep notes made there": their notes past the edges too, kept_notes)
        notes = [np.asarray(self.kept_notes(self.shapes[i]), np.int64).reshape(-1, 4) for i in sels]
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
        self.colour_edit_leave()  # (a colour step being edited: whole first)
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
        if self.keeps_offrange():  # ("Keep notes made there": the parts' notes past the edges merged too, for good)
            new[0]["merge"]["whole"] = True
        from notes.engine import keeps_laps
        if any(keeps_laps(self.shapes[i]) for i in pair):  # (a Domino paste's overlaps kept, Multi too: overlap hunt
            for sh in new:                                  # 2026-11-08)
                sh["paste_laps"] = True
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
