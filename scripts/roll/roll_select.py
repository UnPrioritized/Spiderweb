"""The Select tool's mouse work on the piano roll, its press / drag / let go together (split out of pianoroll.on_press /
on_drag / on_release, which only hand the click on): picking shapes (click, Ctrl+click, the next one under the mouse,
a funnel's line / a custom shape's stroke), moving them (snap by the notes or by the kept Select box, Ctrl+drag
copies), drawing a Select box, and the ruler's click / drag (the play line, a box over every key). The Select box's
own work (box_to, box_pick, box_scroll, kept boxes) stays in pianoroll; .claude/rules/roll/select.md."""

import copy

from files.lang import tr
from notes.between import copied as between_copied
from notes.sliced import fresh_marks
from roll.roll_shared import BOX_STILL, CTRL, RULER_TOP, SHIFT, boxes_upright


class SelectTool:
    """Mixin for PianoRoll: the Select tool's press / drag / let go."""

    # ---- the press --------------------------------------------------------------------------------------------------

    def press_select(self, e, pt, kept, hit, on_box):
        """hit: custom_hit at the press (inside the selected custom shape's box: it moves); on_box: the kept Select
        box pressed inside (on_kept_box)."""
        app = self.app
        # (its notes count too; a selected shape under the mouse first, except Ctrl+click: adds the one on top)
        i = self.shape_at(e.x, e.y, prefer_selected=not e.state & CTRL)
        if i is not None and self.by_area and not e.state & CTRL:  # (only a mask's area: a click picks it on
            # let go (already the one selected: the next under it), a drag draws a Select box; its line / notes
            # grab it, user)
            self.area_pick, i = (i, app.sels == {i} and not self.doubled), None
        if i is None and hit and not e.state & CTRL:
            i = app.sel  # anywhere inside the selected custom shape's box moves it
        if i is not None and i not in app.sels and not e.state & CTRL:
            self.box_moving = None  # a shape it didn't select, grabbed inside the box: the box goes (user)
        elif kept and not on_box and i in app.sels and not e.state & CTRL:
            self.box_moving = boxes_upright(kept)[0]  # one it selected, grabbed outside it: it goes along too
            self.box_kept = (self.box_moving, set(app.sels))  # (user; a click there still picks just that one)
        if on_box and e.state & CTRL and app.sels:  # Ctrl inside the kept Select box: a drag moves a COPY of all it selected
            app.push_undo(name=tr("app.duplicate"))  # (user; let go without moving = a Ctrl+click)
            self.dup = {"click": i}
            self.drag = ("move", pt, {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}, None, False,
                         False)
            return
        if i is None and on_box:  # inside the kept Select box: all it selected moves
            app.push_undo(name=tr("pianoroll.move"))
            self.drag = ("move", pt, {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}, None, False,
                         False)
            return
        # clicking the one selected funnel again: its line / curve under the mouse gets highlighted;
        # the one selected custom shape again: its stroke under the mouse gets picked (none: unpicked)
        # (a drawing tool on Domino working as Select: drags only, a click picks nothing more: no part / stroke,
        # no next shape, no "just this one of several"; user took the hunt's pick 2026-11-21)
        tap = not self.as_select
        again = tap and i is not None and app.sels == {i} and app.shapes[i]["kind"] == "funnel"
        part = self.part_at(app.shapes[i], e.x, e.y) if again else None
        if (tap and i is not None and app.sels == {i} and app.shapes[i]["kind"] == "custom"
                and not app.shapes[i].get("text") and "notes" not in app.shapes[i] and not e.state & CTRL):
            again, part = True, ("stroke", self.stroke_at(app.shapes[i], e.x, e.y))
        if part and e.state & CTRL:  # Ctrl+click: highlight just this one too (or not any more)
            app.set_parts(app.parts ^ {part})
            return
        if i is None and not self.select_boxes:  # (Draw on Domino: no box of ours, nothing changes; overlay_roll)
            return
        if i is None:  # a box that selects the shapes it touches (Ctrl: adds them to the ones selected, and
            self.box_more = list(kept or []) if e.state & CTRL else []  # the box to the boxes kept);
            if not e.state & CTRL:  # a click without dragging moves the play line here
                app.select(None)
            elif self.ruler_boxes(kept):  # (Ctrl with the ruler's bands kept: once it's a box they go, with
                self.box_drop = True       # what they selected, user; a Ctrl+click changes nothing)
            self.drag = ("box", self.x2t(e.x), self.y2p(e.y), e.x, e.y, set(app.sels), app.sel)
            self.box_shift0 = bool(e.state & SHIFT)
            return
        # the one selected shape clicked again (not on a line / stroke of its own being picked): let go without
        # dragging = the next shape under the mouse (user: masks, their Masked notes and the pattern lie stacked)
        # (not inside a kept Select box: a click there keeps it, user; not the press of a double-click)
        hard = part and (part[0] != "stroke" or part[1] is not None)
        self.cycle = ((i, e.x, e.y) if tap and app.sels == {i} and not e.state & CTRL and not hard and not on_box
                      and not self.doubled and len(app.shapes) > 1 else None)
        if e.state & CTRL:  # Ctrl+click adds or removes a shape
            app.select(i, toggle=True)
            if i not in app.sels:
                return
        elif i not in app.sels:
            app.select(i)
        elif i != app.sel:
            app.select_many(app.sels, i)
        app.push_undo(name=tr("pianoroll.move"))
        self.grabbed = i
        orig = {j: copy.deepcopy(app.shapes[j]["pts"]) for j in app.sels}
        # clicking one shape of several without dragging selects just that one; clicking the selected funnel
        # without dragging highlights the part under the mouse (none: clears it)
        self.drag = ("move", pt, orig, None if e.state & CTRL or not tap else i, False, part if again else False)

    # ---- moving shapes ------------------------------------------------------------------------------------------------

    def drag_move(self, e):
        if self.box_moving:  # (past the edge the view scrolls: box_scroll)
            self.box_mouse = (e.x, e.y, e.state)
            if self.box_timer is None:
                self.box_timer = self.after_idle(self.box_scroll)
        pt = self.event_pt(e, above=True)
        _, start, orig, one, _, part = self.drag
        db, dp = pt[0] - start[0], 0 if self.time_only else pt[1] - start[1]
        if e.state & SHIFT and self.app.snap_beats():  # (free: from where the press really was, not its snapped
            free = self.event_pt(e, snap=False, above=True)  # place, else the shape sits off the mouse)
            db, dp = free[0] - self.press_beat, 0 if self.time_only else free[1] - self.press_key
        if not (db or dp) and not self.drag[4]:
            return
        if self.dup and self.dup != "done":
            orig = self.copy_moved(orig)
        if self.room is None:  # (each shape stops at beat 0 and the lowest / highest key on its own, the rest go
            self.room = {j: self.move_room(self.as_made(j)) for j in orig}  # on; with the box all stop: user)
            self.onto_grid = ((None, {}) if self.box_moving else
                              (self.note_edges(orig), {j: self.note_edges([j]) for j in orig}))
        fix, side = 0.0, None
        if self.box_moving:  # the kept Select box goes along: it sticks to the grid, not the notes (box_step)
            db, dp = self.box_step(db, dp, e, orig)
        elif self.app.snap_beats() and not e.state & SHIFT and self.onto_grid[0]:  # the nearest sticky point:
            raw = self.event_pt(e, snap=False, above=True)[0] - self.press_beat  # the earliest note's start or
            db, fix, side = self.sticky_move(self.onto_grid[0], raw)               # the last one's end (user)
        go = ({j: (db, dp) for j in orig} if self.box_moving else
              {j: self.in_room(db + fix, dp, e, self.room[j], fix) for j in orig})
        held = [j for j in orig if go[j][0] != db + fix] if side is not None else []
        if held:  # one held back at beat 0: it sits with its own earliest note on the grid, the rest stick by
            own = self.onto_grid[1]  # their own edges (else they took the held one's shift off the grid)
            rest = [own[j] for j in orig if j not in held and own[j]]
            rdb, rfix, _ = (self.sticky_move((min(a for a, _ in rest), max(b for _, b in rest)), raw) if rest
                            else (db, fix, None))
            for j in orig:
                f = self.edge_fix(own[j], 0) if j in held else rfix
                go[j] = self.in_room((db if j in held else rdb) + f, dp, e, self.room[j], f)
        db += fix
        self.drag = ("move", start, orig, one, True, part)
        kept = self.box_kept
        moved = False
        for j, pts in orig.items():
            bj, pj = go[j]
            new = [[b + bj, p + pj] for b, p in pts]
            moved = moved or new != self.app.shapes[j]["pts"]
            self.app.shapes[j]["pts"] = new
        if self.box_moving:
            self.box_kept = ([(b0 + db, top, b1 + db, bottom) if top >= RULER_TOP - 0.5 else  # (a ruler band:
                              (b0 + db, top + dp, b1 + db, bottom + dp)                         # time only)
                              for b0, top, b1, bottom in self.box_moving],
                             set(self.app.sels))
        if moved or self.box_kept != kept:  # (snapped to where they already were: nothing to redo)
            self.app.shape_edited(moving=True)

    def copy_moved(self, orig):
        """Ctrl+drag inside the kept Select box, the first move: copies of the shapes are added and selected, and
        they're what moves (the shapes stay). orig for the copies."""
        app = self.app
        first = len(app.shapes)
        app.shapes += [app.without_cc(copy.deepcopy(app.shapes[j])) for j in sorted(orig)]
        fresh_marks(app.shapes[first:])
        between_copied(app.shapes, first)  # (a whole Add between group: a new group; parts of one: plain shapes)
        new = {first + k: orig[j] for k, j in enumerate(sorted(orig))}
        if len(app.shapes) > first + len(orig):  # (a group copied without its steps: they were added)
            new.update({j: copy.deepcopy(app.shapes[j]["pts"]) for j in range(first + len(orig), len(app.shapes))})
        app.select_many(new, max(new))
        # (the copies' notes aren't made yet: the originals' notes are carried along for them, like a move's (user
        # 2026-11-07: making every note with each copy on its original took 1.7-2.9 s on big projects, and overlaps
        # split the notes between them); made at a rest / let go, or right away when that's quick)
        self.copy_src = {first + k: j for k, j in enumerate(sorted(orig))}
        app.shapes_changed(moving=True)
        self.dup = "done"
        return new

    def release_move(self, e, still):
        if self.time_only and not self.drag[4]:  # the ruler's black band clicked: the play line
            if not self.app.player.running:  # (playing: it stays, user 2026-10-23)
                self.app.set_playhead(self.event_pt(e)[0])
        elif (not self.drag[4] and not self.dup and self.cycle is not None
              and abs(e.x - self.cycle[1]) < 4 and abs(e.y - self.cycle[2]) < 4  # (a real click, not a short drag)
              and (nxt := self.next_at(e.x, e.y, self.cycle[0])) is not None):  # (the selected one clicked: the
            self.cycled = (self.cycle[0], e.x, e.y)  # next under it)
            self.app.select(nxt)
        elif not self.drag[4] and self.dup:  # Ctrl+click in the box: adds / takes out one
            if self.dup["click"] is not None:
                self.app.select(self.dup["click"], toggle=True)
        elif (not self.drag[4] and self.drag[3] is not None and len(self.app.sels) > 1
              and not (self.box_moving and self.box_in)):  # (inside the kept Select box a click keeps the
            #                                                selection and box: user)
            self.app.select(self.drag[3])
        elif not self.drag[4] and self.drag[5] is not False:
            part = self.drag[5]
            if part and part[0] == "stroke":
                self.app.set_stroke(part[1])
            else:
                self.app.set_parts(self.part_group(self.app.selected(), part) if part else (), main=part)

    # ---- the Select box being drawn -----------------------------------------------------------------------------------

    def drag_box(self, e):
        self.box_mouse = (e.x, e.y, e.state)
        self.box_to()
        if self.box_timer is None:
            self.box_scroll()

    def release_box(self, e, still):
        if self.box_area() or self.box_more and e.state & CTRL:
            self.box_pick()
            self.box_kept = (self.box_more + [self.box_area()] if self.box_area() else self.box_more,
                             set(self.app.sels))
            if self.box_resume:  # (the ruler pressed while playing: it plays on from there)
                self.app.start_play(keep=True)
        elif not self.box_area() and not e.state & CTRL and self.area_pick is not None:
            i, alone = self.area_pick  # (a click in a mask's area picks the mask; it again: the next under it)
            nxt = self.next_at(e.x, e.y, i) if alone else None
            if nxt is not None:
                self.cycled = (i, e.x, e.y)
            self.app.select(i if nxt is None else nxt)
        elif not self.box_area() and not e.state & CTRL:
            if not self.app.player.running:  # (a click on empty space: the play line there; playing: it stays,
                self.app.set_playhead(self.event_pt(e)[0])  # user 2026-10-23)
        elif not self.box_area() and self.box_resume:  # (a short Ctrl+drag on the ruler: plays on)
            self.app.start_play(keep=True)

    # ---- the ruler (bar numbers): the play line, dragged sideways a box over every key ------------------------------

    def drag_seek(self, e):
        if abs(e.x - self.drag[2]) >= BOX_STILL:
            self.ruler_box()
            self.on_drag(e)

    def release_seek(self, e, still):
        if self.drag[4]:  # (a Select box / ruler band was kept: it goes with what it selected, like many MIDI
            self.app.select(None)  # programs)
        if self.drag[1]:
            self.app.start_play(keep=True)  # it was playing: carry on from the new spot
