"""Mixed into App: notes kept past the keys / before the start (off-range), their wrapped view and masks."""

import numpy as np
from roll.roll_draw import GHOST_FADE
from notes.engine import NO_NOTES, off_part, past_edges, track_ids
from notes.sliced import cut_group
from notes.merge import has_colours
from notes.mask import SKIP10, as_used, is_mask, mask_view, pinned_slots, silent, slot_rule
from notes.paths import rows_up_to
from notes.app_layout import Channels, all_notes, cc_busy, landing_clashes, wrap_landing
from notes.custom import ROUND

EXACT_RETRY_MS = 250  # ("Exact colours": asked again this often while the mouse is held, App.exact_soon)


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


def alone_slots(sh, notes, alone, keys, ppq, use10, real):
    """Slots for sh's kept notes (start, end, key, ...) from sh's channels worked out ALONE, made whole on a taller
    keyboard (past_edges' lifted shape; alone(shapes, keys) -> (channel 10 used, rendered rows) = all_notes with the
    project's settings), each alone slot -> the slot most of sh's real notes (real: rendered rows, or None) with that
    alone slot are on; one never on the keys moves by the shift most of them made. -1 = not found; None = no lift."""
    got = past_edges(sh, keys, ppq)
    if got is None:
        return None
    lifted, up, room, later = got
    with rows_up_to(room - 1):
        used10, rows = alone([lifted], room)
    rows = np.array(rows, np.int64, copy=True)
    if rows.ndim != 2 or not len(rows):
        return None
    rows[:, 2] -= up
    if later:
        rows[:, :2] -= round(later * ppq)
    return lined_up(rows, used10, notes, use10, real)


def project_slots(a, used10, use10):
    """Slots a made with channel 10 skipped (used10 False) in the project's numbering (use10)."""
    if use10 and not used10:
        return a // 15 * 16 + np.asarray(SKIP10, np.int64)[a % 15]
    return a


def lined_up(rows, used10, notes, use10, real):
    """notes' slots from rows (rendered's columns, one shape's, worked out elsewhere: alone / on a taller keyboard;
    used10 = their channel 10 rule) by start + key, each of those slots -> the slot most of the shape's real notes
    (real: its rendered rows, or None) with it are on; one never on the keys moves by the shift most of them made.
    -1 = not found."""
    if not len(rows):
        return np.full(len(notes), -1, np.int64)
    a = project_slots(rows[:, 4], used10, use10)

    def code(r):  # (start + key as one number; keys stay inside -8192 .. 8191)
        return r[:, 0] * 16384 + (r[:, 2] + 8192)
    rc = code(rows)
    o = np.lexsort((rows[:, 3], rows[:, 1], rc))
    rc, a, fine_rows = rc[o], a[o], rows[o]
    stacked = np.zeros(len(rc), bool)  # (notes sharing a start + key: told apart by end + velocity too, hunt
    same = rc[1:] == rc[:-1]           # 2026-11-14: a paste's stacked tracks all showed the first one's colour)
    stacked[1:] |= same
    stacked[:-1] |= same

    def look(r):
        c = code(r)
        lo, hi = np.searchsorted(rc, c), np.searchsorted(rc, c, "right")
        out = np.where(hi > lo, a[np.minimum(lo, len(rc) - 1)], -1)
        many = hi - lo > 1
        if many.any():
            fr = np.column_stack([rc[stacked], fine_rows[stacked][:, 1], fine_rows[stacked][:, 3]])
            fq = np.column_stack([c[many], r[many, 1], r[many, 3]])
            inv = np.unique(np.concatenate([fr, fq]), axis=0, return_inverse=True)[1].ravel()
            table = np.full(int(inv.max()) + 1, -1, np.int64)
            table[inv[:len(fr)]] = a[stacked]
            got = table[inv[len(fr):]]
            out[many] = np.where(got >= 0, got, out[many])
        return out
    out = look(np.asarray(notes, np.int64))
    if real is None or not len(real):
        return out
    pair = np.column_stack([look(real), real[:, 4]])
    pair = pair[pair[:, 0] >= 0]
    if not len(pair):
        return out
    pairs, counts = np.unique(pair, axis=0, return_counts=True)
    best = {}
    for (k, v), c in zip(pairs.tolist(), counts.tolist()):
        if c > best.get(k, (None, 0))[1]:
            best[k] = (v, c)
    d = pairs[:, 1] - pairs[:, 0]
    ds, dc = np.unique(np.repeat(d, counts), return_counts=True)
    shift = int(ds[dc.argmax()])
    # (a slot the shift takes under 0 moves up by whole channel sets: same colour, slot % channels; tester
    # 2026-11-15: more colours past the keys than on them, laid first, went to -1 and all fell on one slot)
    per = 16 if use10 or used10 else 15

    def moved(k):
        k += shift
        return k if k >= 0 else k + -(k // per) * per
    kinds, back = np.unique(out, return_inverse=True)  # (a few alone slots: one look-up each, not per note)
    return np.array([best[k][0] if k in best else moved(k) if k >= 0 else -1 for k in kinds.tolist()],
                    np.int64)[back.ravel()]


def exact_kept(shapes, off, rendered, make, keys, ppq, use10, run, busy=None):
    """Settings → Piano roll "Exact colours past the keys" (off; user 2026-11-14: people work past the keys like on a
    canvas): off (kept_rows' rows) with each slot worked out for the WHOLE project on a taller keyboard, every
    shape's kept notes in it (other shapes' notes they'd clash with too), each shape's slots lined up with its real
    notes (lined_up). run(shapes, keys, make, busy) -> (channel 10 used, rendered rows) = all_notes with the
    project's settings; make = a notes_tracks; busy = all_notes' (cc_busy: the times CC lines keep a channel), moved
    later with the notes (hunt 2026-11-14). Slow (all the channels again): only in the background (App.exact_soon)."""
    if not len(off):
        return off
    lifts = [past_edges(sh, keys, ppq) for sh in shapes]
    got = [g for g in lifts if g is not None]
    if not got:
        return off
    up = max(g[1] for g in got)
    up = -(-up // 12) * 12  # (whole octaves: a Glissando's picked keys stay real piano keys)
    room = up + max([keys] + [g[2] - g[1] for g in got])
    later = max(round(g[3] * ppq) for g in got)

    def tall(s):  # (every shape's notes with those past the keys, up by `up` keys and `later` ticks: all >= 0)
        n, t = make(s)
        n, t = with_kept(s, n, t, ppq, keys, make)
        n = np.array(n, np.int64, copy=True).reshape(-1, 4) if len(n) else np.zeros((0, 4), np.int64)
        n[:, 2] += up
        n[:, :2] += later
        return n, t
    if busy is not None and later:
        busy = [None if b is None else {t: (s + later, e + later) for t, (s, e) in b.items()} for b in busy]
    used10, rows = run(shapes, room, tall, busy)
    rows = np.array(rows, np.int64, copy=True)
    if rows.ndim != 2 or not len(rows):
        return off
    rows[:, 2] -= up
    rows[:, :2] -= later
    out = off.copy()
    for i, (mine, sel) in zip(*by_owners(rows, rendered, off)):
        slots = lined_up(mine[0], used10, out[sel], use10, mine[1])
        out[sel, 4] = np.where(slots >= 0, slots, out[sel, 4])
    return out


def by_owners(rows, rendered, off):
    """For each shape with kept rows in off: (its number list, [((its rows, its rendered rows), off's rows of it)])."""
    def sliced(a):
        o = np.argsort(a[:, 5], kind="stable")
        s = a[o]
        return lambda i: s[np.searchsorted(s[:, 5], i):np.searchsorted(s[:, 5], i, "right")]
    of_rows, of_real = sliced(rows), sliced(rendered) if len(rendered) else (lambda i: rendered[:0])
    owners = np.unique(off[:, 5])
    o = np.argsort(off[:, 5], kind="stable")
    at = np.searchsorted(off[o, 5], owners)
    ends = np.append(at[1:], len(o))
    return owners.tolist(), [((of_rows(i), of_real(i)), o[a:b]) for i, a, b in zip(owners.tolist(), at, ends)]


def with_kept(sh, notes, tracks, ppq, keys, make):
    """sh's (notes, tracks) with its notes past the keys / before beat 0 added where they really are (keys under 0 or
    over the top, ticks under 0), from the same remembered lifted notes as kept_tracks; like all_notes' wrapped_in,
    unwrapped (exact_kept)."""
    out = past_edges(sh, keys, ppq)
    if out is None:
        return notes, tracks
    lifted, up, room, later = out
    with rows_up_to(room - 1):
        n, t = make(lifted, room)
    if not len(n):
        return notes, tracks
    n, t = off_part(n, t, up, keys, round(later * ppq))
    n = np.asarray(n, np.int64)[:, :4]
    if not len(n):
        return notes, tracks
    a = np.asarray(notes, np.int64).reshape(-1, 4) if len(notes) else np.zeros((0, 4), np.int64)
    both = np.concatenate([a[:, :4], n])
    if tracks is None and t is None:
        tt = None
    else:
        tt = np.concatenate([np.zeros(len(a), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel(),
                             np.zeros(len(n), np.int64) if t is None else np.asarray(t, np.int64).ravel()])
    order = np.argsort(both[:, 0], kind="stable")
    return both[order], None if tt is None else tt[order]


def kept_rows(shapes, rendered, make, keys, ppq, multi, rounds, use10=False, alone=None):
    """App.offrange_rows' work (Keep notes on), from plain data only, so the notes thread can do it beside the notes
    (App._make_coming): shapes = app.shapes as the notes were made, rendered = their notes, make = a notes_tracks,
    multi = Multi channel, rounds = App.rounds_apart(), use10 = the rendered notes' slot rule (App.picture_use10),
    alone = alone_slots' all_notes for one shape (None: guesses stay)."""
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
        src = i if heard(i) is not None else next(
            (kin[k] for k in (("paste", sh.get("paste")), ("cut", cut_group(sh))) if k[1] is not None and k in kin),
            None)
        # ("Repeated colours get new channels": made with its repeats like the real notes, so each kept note's track
        # names its colour AND its repeat, the same lifted notes the real ones come from; user 2026-11-14: a funnel
        # past the keys showed one colour, its plain colour numbers never matched the real notes' repeat tracks.
        # A shape with no real notes to copy from (src None) = its plain colour numbers, as before: a repeat's track
        # read as a slot is a huge number the painter took for an own track, and merges choked on it)
        # (a shape whose tracks ARE its channels (Merged notes, Merge leftovers, Turn into plain notes, Apply mask's
        # copies, pictures: mask.pinned_slots) = exact, nothing guessed; user 2026-11-14: a merged paste's rows left
        # over the top key all fell onto its most-used channel, right again once dragged onto the keys)
        pin = pinned_slots(sh, "auto" if multi else "single")
        rep = not pin and rounds and src is not None and has_colours(sh)
        notes, tracks = kept_tracks(dict(sh, _rounds=True) if rep else sh, make, keys, ppq)
        if not len(notes):
            continue
        notes = np.asarray(notes, np.int64).reshape(-1, 4)
        t = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel()
        slot = t if multi else np.zeros(len(notes), np.int64)
        guessed = None  # (kept notes whose slot is only a guess: nothing on the keys to copy it from)
        if pin and tracks is not None:
            slot = np.asarray(as_used(t, slot_rule(sh), use10), np.int64)
        elif src is not None:
            mine, other = heard(src), shapes[src]
            got = colour_slots_of(other, mine, make, rounds)
            if len(got) == 1:
                slot = np.full(len(notes), got[0], np.int64)
            else:
                real = make(dict(other, _rounds=True) if rounds and has_colours(other) else other)[1]
                u = np.unique(np.asarray(real, np.int64)) if real is not None else np.zeros(1, np.int64)
                # (the most used slot, ties = the lowest; counted by the slots there are: a repeat's slot is billions
                # past the others, and counting every number between asked for 33 GB at once, 2026-11-15)
                vals, counts = np.unique(mine[:, 4], return_counts=True)
                most = int(vals[counts.argmax()])
                slot = repeat_slots(t, u, np.asarray(got, np.int64), most) if rounds and len(u) == len(got) else None
                if slot is not None:
                    guessed = ~np.isin(t & (ROUND - 1), u & (ROUND - 1))  # (a colour never on the keys)
                else:
                    at = np.clip(np.searchsorted(u, t), 0, len(u) - 1)
                    found = (u[at] == t) & (at < len(got))
                    slot = np.where(found, np.asarray(got, np.int64)[np.minimum(at, len(got) - 1)], most)
                    guessed = ~found
        elif multi and tracks is not None or sh.get("into"):  # (laid on tracks: on them in every mode, hunt)
            guessed = np.ones(len(notes), bool)
        # (those from the shape's channels worked out ALONE on a taller keyboard, lined up with its real notes; user
        # 2026-11-14: a shape with more than 15 colours wholly past the keys showed them off by a few channels)
        if alone is not None and guessed is not None and guessed.any() and (src is None or src == i):
            got = alone_slots(sh, notes[guessed], alone, keys, ppq, use10, heard(i))
            if got is not None:
                slot = slot.copy()
                slot[guessed] = np.where(got >= 0, got, slot[guessed])
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

    def offrange_changed(self, remake=True):
        """Settings → Piano roll "Keep notes made there" / its Opacity / the Space settings changed: the kept notes
        worked out again and painted at the new opacity (no undo step: an app setting). remake False (Opacity,
        Wrap them around): the same kept notes, only painted again (hunt 2026-11-14: remaking them threw the Exact
        colours away and took ~1 s at 1 M kept notes)."""
        from roll.roll_draw import OFF_FADE
        OFF_FADE[0] = min(100, max(1, int(self.offrange_opacity.get())))
        if remake or not self.keeps_offrange():
            if any(sh.get("into") for sh in self.shapes):  # (laid shapes count their kept colours: their notes
                self.shapes_changed(now=True)              # may go on other tracks now, all_notes kept)
            self.off_all = self.offrange_rows()
            self.after_idle(self.exact_soon)
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
                        busy=self.cc_busy(mode), own=self.own_channels(), overlaps=self.overlap_rule(), wrap=wrap,
                        kept=self.keeps_offrange())  # (laid shapes on the same tracks as shown)
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
        (Multi) or slot 0. Never part of rendered / drawn: nothing that leaves Spiderweb sees them. A colour step
        being edited: made from the shapes as shown (edit_shapes, its later steps skipped), like the real notes."""
        if not self.keeps_offrange() or self.companion is not None or not self.shapes:
            return NO_NOTES
        return kept_rows(self.edit_shapes(), self.rendered, self.notes_tracks, self.keys, self.ppq,
                         self.channel_mode.get() == "auto", self.rounds_apart(), self.picture_use10,
                         self.alone_maker(self.notes_tracks))

    def alone_maker(self, make, own=None):
        """kept_rows' alone: all_notes for some shapes on keys keys with the project's channel settings now ->
        (channel 10 used, rendered rows). make = a notes_tracks; own = own_channels() (the notes thread passes its
        copy). Read on Tk's thread only (the thread gets it made there: offrange_wanted)."""
        mode, split, ppq = self.channel_mode.get(), self.channel_split, self.ppq
        all16, rounds, order = self.all16(), self.rounds_apart(), self.in_order()
        own = self.own_channels() if own is None else own

        def alone(shapes, keys):
            got = all_notes(shapes, ppq, keys, mode, split, all16, make, rounds=rounds, order=order, own=own)
            return got[1], got[2]
        return alone

    def offrange_wanted(self):
        """What the notes thread needs to work out offrange_rows beside the notes (App._make_coming, whose shapes
        are edit_shapes' too): (Multi channel, Repeated colours get new channels, Exact colours past the keys), or
        None when it can't / needn't (Keep notes off, Draw on Domino). Read on Tk's thread only."""
        if not self.keeps_offrange() or self.companion is not None:
            return None
        return self.channel_mode.get() == "auto", self.rounds_apart(), bool(self.exact_offrange.get())

    def exact_soon(self):
        """After the kept notes are made the quick way (shapes_changed, offrange_changed): with "Exact colours past
        the keys" on, their colours worked out again in a background job of its own (exact_kept, from the notes
        already made: hunt 2026-11-14, a whole notes job redid everything and a click after an edit waited for it),
        taken in when nothing changed meanwhile. Stopped by the next call; while the mouse is held / notes are
        still coming it tries again shortly (hunt: after a drag nothing asked again)."""
        old, self._exact_job = self._exact_job, None
        if old is not None:
            old["stop"] = True
        if self._exact_retry is not None:
            self.after_cancel(self._exact_retry)
            self._exact_retry = None
        if not (self.exact_offrange.get() and len(self.off_all) and self.offrange_wanted()):
            return
        if self.roll.drag or self.scrubbing or self.notes_late:
            self._exact_retry = self.after(EXACT_RETRY_MS, self.exact_soon)
            return
        import threading
        key = self.notes_key()
        job = self.thread_job(key)
        shapes, make = self.thread_shapes(key), self.thread_maker(job, key)
        job["from"] = off = self.off_all
        rendered, use10 = self.rendered, self.picture_use10

        def work():
            try:
                own = Channels(key[11])  # (as own_channels: the loaded tracks given a channel too)
                own.fixed = frozenset(key[12])

                def run(some, k, mk, busy):  # (no Remove overlaps: cuts notes after the channels, same slots, -38 %)
                    made = all_notes(some, key[1], k, *key[3:6], mk, rounds=key[6], order=key[7], keep=key[8],
                                     own=own, busy=busy)
                    return made[1], made[2]
                busy = cc_busy(shapes, key[1], key[3])  # (as App.cc_busy: the channels CC lines keep, hunt)
                job["off"] = exact_kept(shapes, off, rendered, make, key[2], key[1], use10, run, busy)
            except Exception:  # (Stopped, MemoryError...: the quick colours stay)
                job["off"] = None
            job["done"] = True
        self._exact_job = job
        threading.Thread(target=work, daemon=True, name="exact colours").start()
        self.after(30, self._exact_check, job)

    def _exact_check(self, job):
        if job is not self._exact_job or job["stop"]:
            return
        if not job["done"]:
            self.after(30, self._exact_check, job)
            return
        self._exact_job = None
        off = job.get("off")
        if off is None or job["from"] is not self.off_all or self.notes_key() != job["key"]:
            return
        self.off_all = off
        self.made_version += 1
        self.off_masked = self.masked_offrange() if self.mask_preview.get() else None
        self._outgoing = None
        self.track_view()  # (what the piano roll paints: off_drawn; hunt: never refreshed, so never shown)
        self.roll.request_redraw()

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
