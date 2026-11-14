"""Mixed into App: notes kept past the keys / before the start (off-range), their wrapped view and masks."""

import numpy as np
from roll.roll_draw import GHOST_FADE
from notes.engine import NO_NOTES, off_part, past_edges, track_ids
from notes.sliced import cut_group
from notes.merge import has_colours
from notes.mask import is_mask, mask_view, silent
from notes.paths import rows_up_to
from notes.app_layout import all_notes, landing_clashes, wrap_landing
from notes.custom import ROUND


def kept_tracks(sh, make, keys, ppq):
    """App.offrange_tracks' work (Keep notes on): make = a notes_tracks (App's, or the notes thread's own)."""
    empty = np.zeros((0, 4), np.int64), None
    if sh.get("hz") or silent(sh):  # (a mask whose own notes don't sound: none there either, like all_notes)
        return empty
    out = past_edges(sh, keys, ppq)
    if out is None:
        return empty
    lifted, up, room, later = out
    with rows_up_to(room - 1):
        notes, tracks = make(lifted, room)
    if not len(notes):
        return empty
    return off_part(notes, tracks, up, keys, round(later * ppq))


def kept_rows(shapes, rendered, make, keys, ppq, multi, rounds):
    """App.offrange_rows' work (Keep notes on), from plain data only, so the notes thread can do it beside the notes
    (App._make_coming): shapes = app.shapes as the notes were made, rendered = their notes, make = a notes_tracks,
    multi = Multi channel, rounds = App.rounds_apart()."""
    if not shapes:
        return NO_NOTES
    parts = []
    owners = rendered[:, 5]
    order = np.argsort(owners, kind="stable") if len(rendered) else None

    def heard(i):  # (shape i's real notes as rendered, or None)
        if order is None:
            return None
        lo, hi = np.searchsorted(owners, i, sorter=order), np.searchsorted(owners, i, "right", sorter=order)
        return rendered[order[lo:hi]] if hi > lo else None
    kin = {}  # (pieces of one paste / one cut shape: a piece wholly out there takes its colours from one heard)
    for j, s in enumerate(shapes):
        for k in (("paste", s.get("paste")), ("cut", cut_group(s))):
            if k[1] is not None and k not in kin and heard(j) is not None:
                kin[k] = j
    for i, sh in enumerate(shapes):
        # ("Repeated colours get new channels": made with its repeats like the real notes, so each kept note's track
        # names its colour AND its repeat, the same lifted notes the real ones come from; user 2026-11-14: a funnel
        # past the keys showed one colour, its plain colour numbers never matched the real notes' repeat tracks)
        notes, tracks = kept_tracks(dict(sh, _rounds=True) if rounds and has_colours(sh) else sh, make, keys, ppq)
        if not len(notes):
            continue
        notes = np.asarray(notes, np.int64).reshape(-1, 4)
        t = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel()
        slot = t if multi else np.zeros(len(notes), np.int64)
        src = i if heard(i) is not None else next(
            (kin[k] for k in (("paste", sh.get("paste")), ("cut", cut_group(sh))) if k[1] is not None and k in kin),
            None)
        if src is not None:
            mine, other = heard(src), shapes[src]
            got = colour_slots_of(other, mine, make, rounds)
            if len(got) == 1:
                slot = np.full(len(notes), got[0], np.int64)
            else:
                real = make(dict(other, _rounds=True) if rounds and has_colours(other) else other)[1]
                u = np.unique(np.asarray(real, np.int64)) if real is not None else np.zeros(1, np.int64)
                most = int(np.bincount(mine[:, 4] - mine[:, 4].min()).argmax() + mine[:, 4].min())
                slot = repeat_slots(t, u, np.asarray(got, np.int64), most) if rounds and len(u) == len(got) else None
                if slot is None:
                    at = np.clip(np.searchsorted(u, t), 0, len(u) - 1)
                    found = (u[at] == t) & (at < len(got))
                    slot = np.where(found, np.asarray(got, np.int64)[np.minimum(at, len(got) - 1)], most)
        parts.append(np.column_stack([notes, slot, np.full(len(notes), i, np.int64)]))
    return np.concatenate(parts) if parts else NO_NOTES


SHIFT = ROUND.bit_length() - 1  # (a track = colour + ROUND x repeat: custom.with_rounds)
MOST_TABLE = 1 << 22


def repeat_slots(t, u, got, most):
    """Kept notes' slots with "Repeated colours get new channels" (user 2026-11-14): t = each kept note's track
    (colour + ROUND x repeat), u = the real notes' tracks (sorted, unique), got = u's slots. A track the real notes
    have = its slot; a repeat only the kept part reaches = the slot of the LOWEST real repeat of the same colour (a
    slot shows colour = slot % channels, so the colours carry on across the edge); a colour the real notes never
    have = most. One look-up table over (repeat, colour), a gather per note (two searches over every note took a
    third longer). None when the table would be too big (the caller searches instead)."""
    mask = ROUND - 1
    ub, ur = u & mask, u >> SHIFT
    tr, tb = t >> SHIFT, t & mask
    r0, r1 = min(int(ur.min()), int(tr.min())), max(int(ur.max()), int(tr.max()))
    b = max(int(ub.max()), int(tb.max())) + 1
    if (r1 - r0 + 1) * b > MOST_TABLE:
        return None
    lowest = np.full(b, most, np.int64)
    o = np.lexsort((ur, ub))
    first = np.ones(len(o), bool)
    first[1:] = ub[o][1:] != ub[o][:-1]
    lowest[ub[o][first]] = got[o][first]
    table = np.tile(lowest, r1 - r0 + 1)
    table[(ur - r0) * b + ub] = got
    tr -= r0
    tr *= b
    tr += tb
    return table[tr]


def colour_slots_of(sh, mine, make, rounds):
    """App.colour_slots without the App (its docstring says what): make = a notes_tracks (the notes thread's own
    too), rounds = App.rounds_apart()."""
    vals, counts = np.unique(mine[:, 4], return_counts=True)
    most = int(vals[counts.argmax()])
    n, t = make(dict(sh, _rounds=True) if rounds and has_colours(sh) else sh)
    n = np.asarray(n, np.int64).reshape(-1, 4)
    colour = (np.unique(np.asarray(t, np.int64), return_inverse=True)[1].ravel() if t is not None
              else np.zeros(len(n), np.int64))
    if not len(colour) or colour.max() == 0:
        return [most]
    have = mine[:, 0] * 1024 + mine[:, 2]
    o = np.argsort(have, kind="stable")
    have, slot = have[o], mine[o, 4]
    want = n[:, 0] * 1024 + n[:, 2]
    lo, hi = np.searchsorted(have, want), np.searchsorted(have, want, side="right")
    many = hi - lo  # (rendered notes on each note's start + key)
    each = np.repeat(lo, many) + np.arange(int(many.sum())) - np.repeat(np.cumsum(many) - many, many)
    colours = int(colour.max()) + 1
    pair = np.repeat(colour, many) * len(vals) + np.searchsorted(vals, slot[each])
    weight = np.bincount(pair, 1.0 / np.repeat(many, many), colours * len(vals)).reshape(colours, len(vals))
    best = weight.max(axis=1)
    out, taken = [most] * colours, set()
    for k in sorted(range(colours), key=lambda k: -best[k]):  # (the surest colours first)
        if best[k] <= 0:
            continue  # (none of its notes found: the slot most notes are on)
        order = np.argsort(-weight[k], kind="stable")
        pick = order[0]
        for j in order:
            if weight[k, j] < best[k] * 0.5:
                break
            if int(vals[j]) not in taken:
                pick = j
                break
        out[k] = int(vals[pick])
        taken.add(out[k])
    return out


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
        if not self.keeps_offrange():
            return np.zeros((0, 4), np.int64), None
        return kept_tracks(sh, self.notes_tracks, self.keys, self.ppq)

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
        return kept_rows(self.shapes, self.rendered, self.notes_tracks, self.keys, self.ppq,
                         self.channel_mode.get() == "auto", self.rounds_apart())

    def offrange_wanted(self):
        """What the notes thread needs to work out offrange_rows beside the notes (App._make_coming): (Multi
        channel, Repeated colours get new channels), or None when it can't / needn't (Keep notes off, Draw on
        Domino, a colour step being edited: the shown list isn't app.shapes). Read on Tk's thread only."""
        if not self.keeps_offrange() or self.companion is not None or self.edit_shapes() is not self.shapes:
            return None
        return self.channel_mode.get() == "auto", self.rounds_apart()

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
