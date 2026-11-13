"""The line tools part of Join / Split: Flip each line, Split pieces, plain copies (a mixin of App; moved out of join_split.py)."""

from tkinter import messagebox
import numpy as np
from files.lang import tr, trn
from notes.lines import flipped_lines, lined_up, note_lines, overlap_losers
from notes.passengers import has_cc, split_cc
from files.own_tracks import COUNT_MOST, FILE0, clean_name, from_rows, full_plan, into_ids, new_id, with_port
from notes.engine import PORTS, channels_of
from notes.loaded import clean_extract, extract_groups, held_extract
from window.join_split import by_owner, line_tracks, multi_slots, without10


class JoinLines:
    def line_targets(self):
        """The selected shapes the line tools work on (Flip each line…; notes/lines.py): Fill gaps' (not Hz bass,
        images or masks). Drawn ones become plain notes in the same step (lines_window asks first)."""
        return self.fill_targets()

    def line_count(self, gap):
        """How many lines the line tools' shapes have (each shape's own colours = its tracks), or None: no notes."""
        got = self.line_targets()
        multi = self.multi_kept()[1]  # (the same memo as Fill gaps / plain_copies: (use10, notes); storing the notes
        # alone there made Flip each line fail on its Flip button, 2026-11-08)
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
        self.colour_edit_leave()  # (a colour step being edited: whole first)
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
        self.made_by(new, "line_flip")
        gone = sorted(emptied)
        lost = clean_extract([g for i in gone for g in extract_groups(held_extract(self.shapes[i]))])
        if lost and new:  # (its loaded notes stay out of the file, like Fill gaps: hunt 2026-11-07)
            heir = new[min(new)]
            heir["extract"] = clean_extract(extract_groups(heir.get("extract")) + extract_groups(lost))
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
        self.colour_edit_leave()  # (a colour step being edited: whole first)
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
        self.made_by(new, "split_lines")
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

    def plain_copies(self, got, moved=None, own=False, together=None, whole=False, kept_after=False):
        """Turn into plain notes' new shapes: {i: a shape of shape i's notes exactly as they sound now, each note on
        the channel it has now} for the shapes in got that have notes. moved(rows): their rendered rows changed
        first (Gate sensitive merge: slid). own: the shape's own notes, not as other shapes overlapping them cut
        them (Single channel; merge slides it away from them, hunt), each on its channel all the same.
        together({i: rows}) -> {i: rows}: every shape's rows changed at once (Fill gaps: key rows across shapes).
        whole: the notes as laid in Multi channel in every mode (Fill gaps: Single channel's merging left out).
        With "Keep notes made there" on, the copies keep the shape's notes past the keys / before beat 0 (user
        2026-11-08: plain notes can hold them): added before moved, or after it with kept_after (moved picks rows
        by place: the track list's box cut, its rest keeps them)."""
        keep = self.keeps_offrange()
        if self.notes_late:  # (notes still catching up with a drag: made now)
            self.shapes_changed(now=True)
        use10, multi = self.multi_kept() if whole else self.multi_rendered()  # (Fill gaps: the hint's already)
        shown = by_owner(multi if whole else self.rendered)  # (each shape's rows sliced out, not searched for:
        multi_of = shown if whole else by_owner(multi)       # thousands of shapes, hunt 10-25)
        new = {}
        auto = self.channel_mode.get() == "auto" or whole
        laid_rows = {}
        for i in got:
            rows = shown(i)
            sh = self.shapes[i]
            if not (own or whole or moved or together):  # (Turn into plain notes, Lasso / Slice of drawn shapes)
                rows = self.own_heard(i, rows)
            slots = rows if auto else multi_of(i)
            all_kept = False
            if own:
                n = np.asarray(self.notes_of(sh), np.int64).reshape(-1, 4)
                rows = np.column_stack([n, np.zeros((len(n), 1), np.int64), np.full((len(n), 1), i, np.int64)])
                slots = self.own_slots(i, use10)
                if not len(rows) and keep:  # (all of it past the edges: the kept notes, painted
                    off = self.off_all                          # on their colours' slots: App.offrange_rows)
                    rows = slots = np.array(off[off[:, 5] == i], np.int64)
                    all_kept = True
            if len(rows):  # (a shape with no notes stays as it is)
                if self.in_own_track(sh):  # (laid on tracks: the copy is laid on them too, each note's colour =
                    rows = shown(i).copy()  # its track's place in the shape's list)
                    place = {s: k for k, s in reversed(list(enumerate(self.laid_slots(sh))))}
                    ids, back = np.unique(rows[:, 4], return_inverse=True)
                    rows[:, 4] = np.array([place.get(int(s), 0) for s in ids], np.int64)[back.ravel()]
                elif own or not auto:
                    rows = multi_slots(rows, slots)
                # (past the edges: the hidden part too, hunt 2026-11-07; a merge's own notes with "Keep notes made
                # there": the notes it slid with, merge_mover's kept_notes)
                if whole and not own or own and keep and not all_kept or keep and not own and moved is None:
                    rows = self.with_hidden(sh, rows)
                laid = self.track_slots_in(rows)  # (its CCs' tracks by these notes' slots, before any slide)
                laid_rows[i] = (rows, {i: laid(i)} if "cc" in sh else {})
        if together:
            changed = together({i: r for i, (r, _) in laid_rows.items()})
            laid_rows = {i: (changed[i], cc) for i, (_, cc) in laid_rows.items() if i in changed}
        for i, (rows, cc_slots) in laid_rows.items():
            sh = self.shapes[i]
            if moved:
                rows = moved(rows)
                if kept_after and keep and len(rows):
                    rows = self.with_hidden(sh, rows)
            if not len(rows):  # (none of them wanted: merge leftovers; Fill gaps: all lost to same-start notes)
                continue
            new[i] = without10(self.copied(rows, False, True, cc_slots.get), use10)
            new[i]["made"] = "plain"
            from notes.engine import keeps_laps
            if keeps_laps(sh):  # (a Domino paste's overlaps kept as they were, Multi too: overlap hunt 2026-11-08)
                new[i]["paste_laps"] = True
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
