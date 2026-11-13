"""Mixed into App: flip, rotate and stretch of the selected shapes."""

import copy
import numpy as np
from notes.joined import all_tumours
from roll.roll_shared import cached_path
from notes.fx import flip_shape, flipped as fx_flipped, with_turn as fx_turned, turn_shape
from notes.ripples import grows_free
from notes.funnel import inside_out, turned_curve
from notes.between import keep_pushes, push_sizes, whole_groups
from notes.merge import velocity_reversed as merge_vrev
from notes.pattern import moved_formulas
from notes.engine import past_edges
from notes.paths import rows_up_to
from notes.sliced import steps_kept, turned_piece
from files.lang import tr


class AppTransform:
    def flip(self, sideways, whole=False):
        """Mirror the selected shapes as a group: sideways (time) or upside down (pitch).
        With a funnel's curves highlighted: those curves (end to end / inside out)."""
        if not whole and self.roll.curve_parts():
            return self.roll.set_curves(turned_curve if sideways else inside_out)
        if not whole and self.picked():
            return self.roll.flip_stroke(*self.picked(), sideways)
        if not self.sels:
            return
        shapes = [self.shapes[i] for i in sorted(self.sels)]
        k = 0 if sideways else 1
        vals = [pt[k] for sh in shapes for pt in cached_path(sh)]
        mid2 = min(vals) + max(vals)  # twice the middle
        even = self.notes_middle(shapes, sideways, mid2)
        self.push_undo(name=tr("app.flip_sideways") if sideways else tr("app.flip_upside_down"))
        sizes = push_sizes(self.shapes, whole_groups(self.shapes, self.sels))  # (Add between pushes flip too)
        for sh in shapes:
            steps = steps_kept(sh)  # (a piece of a shape with pages: a flip step too, it stays a piece)
            frm, was = sh["pts"], sh.get("fx")
            fx = fx_flipped(was, "time" if sideways else "keys", steps) if was or steps else was
            # (the true middle for a shape given a flip step: its pages' notes come from its drawing flipped back in
            # its own box (engine.unflipped / turned_back), that gives back the drawing it had; and for a text:
            # rebuilt, it slides onto its anchor by whole ticks, a box moved part of a tick came back a tick off)
            m2 = mid2 if fx is not was or sh.get("text") else even
            if sh.get("merge"):  # (its parts' velocities: each sideways flip turns them round, merge.py)
                sh["merge"]["vrev"] = merge_vrev(sh) != sideways
            if sh.get("unmerge") and sideways:  # (kept as separate shapes: the shape Split gives back, the same)
                sh["unmerge"]["vrev"] = not sh["unmerge"].get("vrev")
            flip_shape(sh, sideways, m2)
            turned_piece(sh, frm, lambda w: flip_shape(w, sideways, m2))  # (a funnel piece stays one)
            if fx is was and fx:  # (no flip step: flip_shape's own steps kept, a merged shape's velocity mirrored)
                pass
            elif fx:  # (its note tool pages' result is flipped too, fx.py)
                sh["fx"] = fx
            else:
                sh.pop("fx", None)
        keep_pushes(self.shapes, sizes, lambda v: (-v[0], v[1]) if sideways else (v[0], -v[1]))
        self.roll.move_kept_box(lambda b, p: (even - b, p) if sideways else (b, even - p))  # (flips too)
        self.sync_panel()
        self.shapes_changed()

    def notes_middle(self, shapes, sideways, mid2):
        """Twice the middle a flip mirrors shapes around: their drawings' own notes' middle (first start + last end,
        lowest + highest key, as fx.notes_box), so each column / row lands on its partner and the flipped notes are
        the mirror note for note (user 2026-10-18: the drawing's middle between two key rows gave a different
        heart). mid2 (the drawing's) when none has notes of its own (Hz bass, pictures), or with Ripples growing
        free (rings cut off at the piano roll's edges move the notes' middle: flipping twice drifted)."""
        if any(grows_free(sh) for sh in shapes):
            return mid2
        got = [self.whole_notes({k: v for k, v in sh.items()
                                 if k not in ("fx", "_m", "sustain", "range", "subtract", "extend")})
               for sh in shapes if not sh.get("hz") and "picture" not in sh]
        got = [n for n in got if len(n)]
        if not got:
            return mid2
        a = np.concatenate(got)
        if sideways:
            return (int(a[:, 0].min()) + int(a[:, 1].max())) / self.ppq
        return int(a[:, 2].min()) + int(a[:, 2].max())

    def whole_notes(self, sh):
        """sh's notes, a shape past the edges (engine.past_edges) WHOLE: the parts before beat 0 / past the keys
        kept, not cut off (a flip mirrors round the whole shape's middle and stays in place; user 2026-11-06: it
        jumped right, onto the notes left after the cut)."""
        out = past_edges(sh, self.keys, self.ppq)
        if out is None:
            return self.notes_of(sh)[:, :3]
        lifted, up, room, later = out
        with rows_up_to(room - 1):
            n = np.array(self.notes_tracks(lifted, room)[0][:, :3], copy=True)
        n[:, 2] -= up
        n[:, :2] -= round(later * self.ppq)
        return n

    def rotate(self, clockwise, whole=False):
        """Turn the selected shapes 90 degrees as a group, around their middle, as they look on screen
        (so the current zoom decides how many beats one key becomes). A stroke picked: just it."""
        if not whole and self.picked():
            return self.roll.turn_stroke(*self.picked(), clockwise)
        if not self.sels or self.roll.sx is None:
            return
        shapes = [self.shapes[i] for i in sorted(self.sels)]
        path = [pt for sh in shapes for pt in cached_path(sh)]
        bs, ps = [b for b, _ in path], [p for _, p in path]
        cb, cp = (min(bs) + max(bs)) / 2, (min(ps) + max(ps)) / 2
        r = self.roll.sy / self.roll.sx  # beats per key on screen
        sign = 1 if clockwise else -1
        limit = self.roll.limits(shapes)  # (turned past an edge: pushed back inside, user)
        self.push_undo(name=tr("app.turn_90"))
        sizes = push_sizes(self.shapes, whole_groups(self.shapes, self.sels))  # (Add between pushes turn too)
        frms = [sh["pts"] for sh in shapes]
        for sh in shapes:
            steps = steps_kept(sh)  # (a piece of a shape with pages: a turn step too, it stays a piece)
            if sh.get("merge"):  # (a turn leaves its parts' velocities as they are)
                sh["merge"]["vrev"] = merge_vrev(sh)
            turn_shape(sh, clockwise, r, cb, cp)
            if sh.get("fx") or steps:  # (its note tool pages' result is turned too, fx.py)
                fx = fx_turned(sh.get("fx"), 90 if clockwise else -90, r, steps)
                if fx:
                    sh["fx"] = fx
                else:
                    sh.pop("fx", None)
        keep_pushes(self.shapes, sizes, lambda v: (sign * v[1] * r, -sign * v[0] / r))
        db, dp = self.roll.push_in(self.roll.reach(shapes), limit)
        if db or dp:
            for sh in shapes:
                sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]

        def turned(w):
            turn_shape(w, clockwise, r, cb, cp)
            w["pts"] = [[b + db, p + dp] for b, p in w["pts"]]
        for sh, frm in zip(shapes, frms):  # (a funnel piece stays one)
            turned_piece(sh, frm, turned)
        self.roll.move_kept_box(lambda b, p: (cb + sign * (p - cp) * r + db, cp - sign * (b - cb) / r + dp),
                                turned=True)  # (too)
        self.shapes_changed()  # (first: notes made in the background, the panel's Recolour row waits for them)
        self.sync_panel()

    @staticmethod
    def stretched(sh, ab, kx, ap, ky):
        """A copy of shape sh stretched kx times sideways from beat ab and ky times up / down from pitch ap (a
        Select box's side dragged), still looking the same stretched: arcs / freehand as round, its tumours,
        formulas and text along. Not a Hz bass's notes (user, like its box's corners)."""
        new = copy.deepcopy(sh)
        fn = lambda b, p: (ab + (b - ab) * kx, ap + (p - ap) * ky)
        new["pts"] = [[round(v, 9) + 0.0 for v in fn(b, p)] for b, p in sh["pts"]]  # (no float noise: roll_custom.tidy)
        r = abs(kx / ky)  # (k = beats per key where it looks round: see rotate)
        if new["kind"] == "arc" or new["kind"] == "free" and "k" in new:
            new["k"] = new.get("k", 1.0) * r
        if new.get("text"):
            new["text"]["k"] = new["text"].get("k", 1.0) * r
        for tm in all_tumours(new):  # (size in keys, the rest in beats)
            tm["k"] *= r
            tm["size"] *= abs(ky)
            tm["length"] *= abs(kx)
            tm["dist"] *= abs(kx)
            tm["ease"] = tm.get("ease", 0.0) * abs(kx)
        moved_formulas(new, fn)
        return new  # (a Hz bass's notes keep their lengths: its box only limits where they sound, user)
