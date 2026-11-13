"""Mixed into App: notes kept past the keys / before the start (off-range), their wrapped view and masks."""

import numpy as np
from roll.roll_draw import GHOST_FADE
from notes.engine import NO_NOTES, off_part, past_edges, track_ids
from notes.sliced import cut_group
from notes.merge import has_colours
from notes.mask import is_mask, mask_view, silent
from notes.paths import rows_up_to
from notes.app_layout import all_notes, landing_clashes, wrap_landing


class AppOffrange:
    def keeps_offrange(self):
        """Settings "Keep notes made there" on AND some space to keep them in (user 2026-11-08: the tick is greyed
        without Space past the keys / before the start, so then it counts as off)."""
        return bool(self.keep_offrange.get() and (self.space_keys.get() > 0 or self.space_bars.get() > 0))

    def offrange_tracks(self, sh):
        """(notes, tracks) sh makes in the space past the keys / before beat 0 (engine.off_part) while "Keep notes
        made there" is on; empty otherwise. KEPT APART from notes_tracks: the real notes (export, playback, Copy to
        Domino, note count...) never hold them. Made from the same remembered lifted notes as edge_made's, so this
        costs no note making of its own. Nothing to save: they come from the shape's drawing again."""
        empty = np.zeros((0, 4), np.int64), None
        if not self.keeps_offrange() or sh.get("hz") or silent(sh):  # (a mask whose own notes don't sound: none
            return empty                                                  # there either, like all_notes)
        out = past_edges(sh, self.keys, self.ppq)
        if out is None:
            return empty
        lifted, up, room, later = out
        with rows_up_to(room - 1):
            notes, tracks = self.notes_tracks(lifted, room)
        if not len(notes):
            return empty
        return off_part(notes, tracks, up, self.keys, round(later * self.ppq))

    def kept_notes(self, sh):
        """sh's (start, end, key, velocity) notes for the tools: with "Keep notes made there" on, its notes past the
        keys / before beat 0 too (the whole shape, a note across beat 0 whole: engine.whole_notes_tracks' way, from
        the remembered notes); else its notes as made (notes_of)."""
        if not self.keeps_offrange() or sh.get("hz"):
            return self.notes_of(sh)
        out = past_edges(sh, self.keys, self.ppq)
        if out is None:
            return self.notes_of(sh)
        lifted, up, room, later = out
        with rows_up_to(room - 1):
            n = np.array(self.notes_tracks(lifted, room)[0][:, :4], np.int64, copy=True)
        n[:, 2] -= up
        n[:, :2] -= round(later * self.ppq)
        return n

    def offrange_changed(self):
        """Settings → Piano roll "Keep notes made there" / its Opacity / the Space settings changed: the kept notes
        worked out again and painted at the new opacity (no undo step: an app setting)."""
        from roll.roll_draw import OFF_FADE
        OFF_FADE[0] = min(100, max(1, int(self.offrange_opacity.get())))
        self.off_all = self.offrange_rows()
        self.made_version += 1
        self.off_masked = self.masked_offrange() if self.mask_preview.get() else None
        self._outgoing = None
        self.track_view()
        self.roll.request_redraw()
        self.update_status()  # (the red "+n off-range" count)
        self.play_changed()  # (Wrap them around: heard from now on, or no longer)

    def wraps_offrange(self):
        """Settings → Piano roll "Wrap them around in exports and Domino copies" on (and Keep notes working)."""
        return self.keeps_offrange() and bool(self.wrap_offrange.get())

    def outgoing(self):
        """The notes that leave Spiderweb (Export MIDI / to Domino, Copy to Domino, Spiderweb Notes, playback): the
        rendered notes, + with "Wrap them around" on the kept notes past the keys put back inside (all_notes wrap=:
        key mod the project's keys, before beat 0 never; overlaps / channels worked out with them like any other
        note in that mode). Rows like rendered's. Made only when asked (and kept until the notes change), so the
        piano roll and the tools never pay for it; never part of app.rendered (the tools would see them twice)."""
        return self.outgoing_layout()[0]

    def outgoing_layout(self):
        """(App.outgoing's notes, their voice_slots {shape: {track: slot}}): the export's own channel layout, so the
        Hz basses' CC lines and pasted notes' CCs go on the channels their notes are WRITTEN on (hunt 2026-11-08:
        wrapped notes moved a Hz bass to another channel, its Cutoff stayed on the old one). Wrap off / nothing
        wrapped = (rendered, voice_slots) as always. A colour step being edited (its later steps skipped on the piano
        roll, panel_recolour.edit_shapes): the WHOLE result all the same."""
        wrap = self.wraps_offrange() and bool(len(self.off_all))
        hidden = self.edit_shown
        if not wrap and not hidden:
            return self.rendered, self.voice_slots
        key = (self.made_version, id(self.rendered), id(self.off_all), self.ppq, self.keys, self.channel_mode.get(),
               wrap, hidden)
        if self._outgoing is not None and self._outgoing[0] == key:
            return self._outgoing[1]
        mode = self.channel_mode.get()
        got = all_notes(self.shapes, self.ppq, self.keys, mode, self.channel_split, self.all16(), self.notes_tracks,
                        rounds=self.rounds_apart(), order=self.in_order(), keep=self.keep_colours.get(),
                        busy=self.cc_busy(mode), own=self.own_channels(), overlaps=self.overlap_rule(), wrap=wrap)
        self._outgoing = (key, (got[2], got[4]))
        return self._outgoing[1]

    def wrapped_rows(self):
        """Where the kept notes past the keys LAND with "Wrap them around" on, for the piano roll ("Show wrapped
        notes", user 2026-11-08): (rows, boxes, clash). rows = off_drawn's rows put where they land by wrap_landing
        (the export's own rule: shown = written), only the selected shapes' in "Selected shapes", every shown one in
        "Always", none when "Off". boxes = [(owner, t0, t1, k0, k1)] each landing area (a shape's notes from over the
        top land at the bottom, from under key 0 at the top: one box each), only in "Selected shapes" (the dashed
        outline). clash = (start, end, key) stretches where a landing note sits on a real note on the same key (the
        export will see them overlap): drawn red. Kept until something it reads changes."""
        none = (NO_NOTES, [], np.zeros((0, 3), np.int64))
        mode = self.show_wrapped.get()
        off = self.off_drawn
        if mode == "off" or not len(off) or not self.wraps_offrange():
            if self._wrapped is not None:
                self._wrapped = None
                self.wrap_version += 1
            return none
        sels = frozenset(self.sels) if mode == "selected" else None
        key = (self.made_version, self.off_version, id(off), id(self.rendered), sels, mode, self.keys)
        if self._wrapped is not None and self._wrapped[0] == key:
            return self._wrapped[1]
        if sels is not None:
            off = off[np.isin(off[:, 5], list(sels))] if sels else off[:0]
        rows, ok = wrap_landing(off, self.keys)
        boxes = []
        if mode == "selected" and len(rows):
            below = off[ok, 2] < 0  # (from under key 0: lands at the top)
            for owner in np.unique(rows[:, 5]).tolist():
                mine = rows[:, 5] == owner
                for side in (below, ~below):
                    r = rows[mine & side]
                    if len(r):
                        boxes.append((owner, int(r[:, 0].min()), int(r[:, 1].max()), int(r[:, 2].min()),
                                      int(r[:, 2].max())))
        got = rows, boxes, landing_clashes(rows, self.rendered)
        self._wrapped = (key, got)
        self.wrap_version += 1
        return got

    def export_view(self, on):
        """W held on the piano roll (user 2026-11-08): the piano roll shows exactly what the export gets: drawn =
        App.outgoing (wrapped notes solid where they land, the track list's one-track view applied like track_view),
        the kept notes past the keys, their landing copies, red marks and dashed boxes hidden (roll_draw.note_rects).
        Only with Keep notes + Wrap on; off = back to the normal view."""
        on = bool(on) and self.wraps_offrange()
        if on == self.export_held:
            return
        self.export_held = on
        self.track_view()
        self.roll.request_redraw()

    def export_drawn(self):
        """track_view's last step while W is held: drawn from the export's notes instead of the piano roll's."""
        out = self.outgoing()
        self.drawn, self.behind_owner = self.track_list.filtered(out, track_ids(out[:, 4]), len(self.shapes))
        self.picture_owners = self.picture_owners_all if self.behind_owner is None else np.append(
            self.picture_owners_all, False)

    def scrub_wrapped(self):
        """Right-drag listening's wrapped notes (user 2026-11-08: heard whenever "Wrap them around" is on, like Play,
        whatever "Show wrapped notes" says): every shown kept note (off_drawn: hidden shapes / tracks never) put where
        it lands by wrap_landing, the export's own rule. Rows like rendered's; kept until off_drawn / keys change, so
        a mouse move only picks rows."""
        off = self.off_drawn
        if not len(off) or not self.wraps_offrange():
            return NO_NOTES
        key = (self.made_version, self.off_version, id(off), self.keys)
        got = getattr(self, "_scrub_wrap", None)
        if got is None or got[0] != key:
            got = self._scrub_wrap = (key, wrap_landing(off, self.keys)[0])
        return got[1]

    def offrange_rows(self):
        """Every shape's "Keep notes made there" notes as rendered rows (start, end, key, velocity, slot, owner): the
        piano roll paints them see-through (roll_draw OFF_BASE). Each colour on the slot its real notes are on
        (colour_slots), so they look like the rest of the shape; a shape with no real notes: its colour number
        (Multi) or slot 0. Never part of rendered / drawn: nothing that leaves Spiderweb sees them."""
        if not self.keeps_offrange() or self.companion is not None or not self.shapes:
            return NO_NOTES
        rendered, parts = self.rendered, []
        owners = rendered[:, 5]
        order = np.argsort(owners, kind="stable") if len(rendered) else None

        def heard(i):  # (shape i's real notes as rendered, or None)
            if order is None:
                return None
            lo, hi = np.searchsorted(owners, i, sorter=order), np.searchsorted(owners, i, "right", sorter=order)
            return rendered[order[lo:hi]] if hi > lo else None
        kin = {}  # (pieces of one paste / one cut shape: a piece wholly out there takes its colours from one heard)
        for j, s in enumerate(self.shapes):
            for k in (("paste", s.get("paste")), ("cut", cut_group(s))):
                if k[1] is not None and k not in kin and heard(j) is not None:
                    kin[k] = j
        for i, sh in enumerate(self.shapes):
            notes, tracks = self.offrange_tracks(sh)
            if not len(notes):
                continue
            notes = np.asarray(notes, np.int64).reshape(-1, 4)
            t = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel()
            slot = t if self.channel_mode.get() == "auto" else np.zeros(len(notes), np.int64)
            src = i if heard(i) is not None else next(
                (kin[k] for k in (("paste", sh.get("paste")), ("cut", cut_group(sh))) if k[1] is not None and k in kin),
                None)
            if src is not None:
                mine, other = heard(src), self.shapes[src]
                got = self.colour_slots(other, mine)
                if len(got) == 1:
                    slot = np.full(len(notes), got[0], np.int64)
                else:
                    real = self.notes_tracks(dict(other, _rounds=True) if self.rounds_apart() and has_colours(other)
                                             else other)[1]
                    u = np.unique(np.asarray(real, np.int64)) if real is not None else np.zeros(1, np.int64)
                    at = np.clip(np.searchsorted(u, t), 0, len(u) - 1)
                    found = (u[at] == t) & (at < len(got))
                    slot = np.where(found, np.asarray(got, np.int64)[np.minimum(at, len(got) - 1)],
                                    int(np.bincount(mine[:, 4] - mine[:, 4].min()).argmax() + mine[:, 4].min()))
            parts.append(np.column_stack([notes, slot, np.full(len(notes), i, np.int64)]))
        return np.concatenate(parts) if parts else NO_NOTES

    def kept_mask_world(self, pattern=None):
        """The masks worked out over the kept notes past the keys (hunt 2026-11-08, user: masks work on them like on
        any note): the masks' areas only reach key 0 .. the top key, so everything is lifted by `up` whole keys onto
        a taller keyboard (nkeys): (shapes with every drawn mask lifted, notes = the real rows of the shapes with kept
        notes + the kept rows, lifted, a 7th column 1 on the kept ones, nkeys, up, notes_of for those shapes: a lifted
        mask's notes made WHOLE there (engine.whole_notes_tracks), others' as made, lifted). None: no mask or no kept
        note. pattern: the rows the masks cover (App.pattern's; None = rendered)."""
        off = self.off_all
        if not len(off) or not any(map(is_mask, self.shapes)):
            return None
        from notes.engine import whole_notes_tracks
        up = self.keys * max(1, int(self.space_keys.get()))
        nkeys = self.keys + 2 * up
        shapes = [dict(sh, pts=[[b, p + up] for b, p in sh["pts"]], _up=up) if is_mask(sh) and "pts" in sh else sh
                  for sh in self.shapes]
        real = self.rendered if pattern is None else pattern
        real = real[np.isin(real[:, 5], np.unique(off[:, 5]))]
        notes = np.concatenate([np.column_stack([real, np.zeros(len(real), np.int64)]),
                                np.column_stack([off, np.ones(len(off), np.int64)])])
        notes[:, 2] += up
        ppq = self.ppq

        def notes_of(s):
            if "_up" in s:  # (fills make their rows that high too: paths.rows_up_to, as past_edges' shapes)
                with rows_up_to(nkeys - 1):
                    return whole_notes_tracks({k: v for k, v in s.items() if k != "_up"}, ppq, nkeys,
                                              over_top=True)[0]
            n = np.array(self.notes_of(s), np.int64, copy=True).reshape(-1, 4)
            n[:, 2] += up
            return n
        return shapes, notes, nkeys, up, notes_of

    def masked_offrange(self):
        """off_all as the masks' preview shows it (mask.mask_view over kept_mask_world): the kept rows of shapes a
        mask covers as ghosts (velocity -1, painted faint) + the parts that show; None = no mask over kept notes."""
        world = self.kept_mask_world()
        if world is None:
            return None
        import notes.mask as mask_mod
        shapes, notes, nkeys, up, notes_of = world
        saved = dict(mask_mod._shown)  # (mask_view's own memory is the real notes' one: Apply mask reuses it)
        try:
            rows, covered = mask_view(notes, shapes, self.ppq, nkeys, notes_of, None, GHOST_FADE[0] > 0)
        finally:
            mask_mod._shown.clear()
            mask_mod._shown.update(saved)
        if not covered:
            return None
        rows = rows[rows[:, 6] == 1][:, :6].copy()
        rows[:, 2] -= up
        return rows

    def masked_kept_shown(self):
        """For Apply mask: {first mask of a unit: the kept rows (rendered's columns) that unit shows}, worked out in
        kept_mask_world like the preview (mask.worked_now)."""
        from notes.mask import unmasked, worked_now
        world = self.kept_mask_world(self.pattern)
        if world is None:
            return {}
        shapes, notes, nkeys, up, notes_of = world
        units, shown = worked_now(shapes, unmasked(notes, shapes), self.ppq, nkeys, notes_of)
        out = {}
        for unit in units:
            got = shown(unit)[1]
            got = got[got[:, 6] == 1][:, :6].copy() if len(got) else got[:, :6]
            if len(got):
                got[:, 2] -= up
                out[tuple(unit)] = got
        return out
