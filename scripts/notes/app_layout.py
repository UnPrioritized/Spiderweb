"""Making all the notes of the shapes together: overlaps / channels worked out, shapes laid on tracks, the wrap
rule for notes past the edges, the CC times (moved out of window/app.py; touches no Tk)."""

import json
import numpy as np
from notes.areas import ALL_COLOURS, most_colours
from notes.engine import CLASH, OWN0, aligned_ch, auto_count, heard_way, keeps_laps, off_part, own_slot, past_edges, pasted_notes, remove_across, render, resolve_overlaps, split_overlaps
from files.own_tracks import FILE0, into_ids
from notes.custom import ROUND, capped_colours, has_pattern, tracks_apart
from notes.mask import as_used, is_mask, picture_like, pinned_slots, silent, slot_rule
from notes.between import turns as between_turns
from notes.hz_cc import cc_spans
from notes.gateflip import colours_back, unflipped_starts
from notes.recolour import counted_starts, first_starts, is_recoloured, with_most, without_recolour
from notes.sliced import cut_group
from notes.strips import gap_of, has_strips, strip_laps, strip_spans
from notes.hzbass import has_cc, own_channels
from notes.merge import has_colours
from notes import hz_cc
from notes.passengers import spans as passenger_spans
from notes.paths import rows_up_to
from notes.sustain import sustain_on
from roll.roll_draw import track_ranks


class Stopped(Exception):
    """Notes being made in the background (App.notes_rested) aren't wanted any more."""


def can_go_in(sh):
    """True if the shape can be drawn into a track added by hand (Draw into a track group B: pictures, masks, Hz
    basses and Channel per strip shapes keep their own channels for now; pasted notes with CC events go in, their
    events on each colour's track, user 2026-10-29: "you copy from Domino is what you'll get")."""
    return not (picture_like(sh) or is_mask(sh) or "hz" in sh or has_strips(sh))


def recolour_alone(sh):
    """sh as its notes are made outside Multi channel: Recolour left out (in one channel it would only change
    overlaps), except on a Domino paste (its tracks kept in every mode: hunt 2026-11-01)."""
    if "recolour" not in sh:
        return sh
    return with_most(sh, False) if pasted_notes(sh) else without_recolour(sh)


def colours_apart(sh, mode):
    """Does a laid shape keep each colour on its own track in this channels mode? Multi channel: always. Single
    channel / As drawn: only a shape keeping its own channels (an Extract, a picture) or a Domino paste (user
    2026-10-29 / 11-01: what you copied is what you get); other shapes play every colour on their first track."""
    return mode == "auto" or bool(pinned_slots(sh, mode)) or pasted_notes(sh)


def placed_on(sh, own):
    """The tracks the shape was laid on (Draw into a track; own: {id: channel} of the tracks added by hand): its
    into_ids up to the first track added by hand that's gone (from there on: laid again, App.fit_into). Each a
    track added by hand's id (>= 0) or -1 - an automatic track's slot (pinned there, B11). [] = an automatic shape
    (also when its first one is gone, or it can't go in: can_go_in)."""
    out = []
    if not can_go_in(sh):
        return out
    for v in into_ids(sh):
        if v >= 0 and v not in (own or {}):
            break
        out.append(v)
    return out


class Known:
    """placed_on's own for App.fit_into: a track id there = one of yours (by_id: {id: track}, grows as tracks are
    added) or a loaded MIDI's track it knows (files: App.file_tracks)."""

    def __init__(self, by_id, files):
        self.by_id, self.files = by_id, files

    def __contains__(self, v):
        return v in self.by_id or v >= FILE0 and v - FILE0 in self.files

    def __bool__(self):
        return True


def entry_slot(v, own, rule, use10):
    """A placed_on entry's slot now: a track added by hand = engine.own_slot; an automatic track = its slot, as the
    project's channel rule has it now (rule = the shape's "slots10" when laid: mask.as_used)."""
    if v >= 0:
        return own_slot(v, own[v])
    return int(as_used(np.array([-1 - v], np.int64), rule, use10)[0])


def laid_slots_of(sh, ids, own, use10):
    """Each placed_on entry's slot now (entry_slot), in order; a colour keeps its own channel (sh["into_ch"], -1 =
    none: the track's) while it's on the track it was given it on (sh["into_home"]) and that track has no channel
    of its own from Track Properties (own.fixed): an Extract's colours in a loaded MIDI's track (group D), a channel
    dragged into a track of yours or a loaded one (a folder, PLAN #11); else the track's channel (hunt 2026-10-26:
    an Extract laid elsewhere held two channels in one track)."""
    rule = sh.get("slots10") if isinstance(sh.get("slots10"), bool) else None
    chs = sh.get("into_ch") if isinstance(sh.get("into_ch"), list) else []
    home = sh.get("into_home") if isinstance(sh.get("into_home"), list) else None
    fixed = getattr(own, "fixed", ())

    def own_ch(k, v):
        return (v >= 0 and k < len(chs) and chs[k] >= 0 and v not in fixed
                and (home is None or k < len(home) and home[k] == v))
    return [own_slot(v, chs[k]) if own_ch(k, v) else entry_slot(v, own, rule, use10) for k, v in enumerate(ids)]


def own_chs(sh):
    """(channels, homes) one per "into" entry (aligned_ch; -1 = the track's channel; no home given = the track it's
    on: its channel holds there), for fit_into to carry with its colours."""
    into = into_ids(sh)
    got = aligned_ch(dict(sh, into=into)) if "into_ch" in sh else {}
    chs = got.get("into_ch", [-1] * len(into))
    return chs, got.get("into_home", [v if c >= 0 else -1 for v, c in zip(into, chs)])


def put_rest_ch(sh, chs, homes):
    """The colours gone for now's channels (engine.rest_ch) kept on the shape, or dropped when none has one."""
    if any(c >= 0 for c in chs):
        sh["into_rest_ch"], sh["into_rest_home"] = list(chs), list(homes)
    else:
        sh.pop("into_rest_ch", None)
        sh.pop("into_rest_home", None)


def keep_home(sh):
    """Before an Extract's colours are laid on other tracks: the tracks they came from kept (into_home; made before
    it was kept = the tracks it's on now)."""
    if isinstance(sh.get("into_ch"), list) and not isinstance(sh.get("into_home"), list):
        sh["into_home"] = [v for v in into_ids(sh)]


class Channels(dict):
    """App.own_channels' {id: channel}, + fixed = the loaded tracks (FILE0 + n) given a channel in Track
    Properties (laid_slots_of: every colour in them on it)."""
    fixed = frozenset()


def wrap_landing(n, keys):
    """THE wrap rule ("Wrap them around", user 2026-11-08), for kept notes past the keys / before beat 0 (rows
    start, end, key, ...): (the ones that land, put where they land, ok = which rows of n those are). Start cut at
    beat 0 (nothing before it, a note across it starts on it), key mod the project's keys (one over the top = key 0,
    one under key 0 = the top key). Export (wrapped_in) and the piano roll's landing spots (App.wrapped_rows) both
    use this, so what's shown is what's written."""
    n = np.array(n, copy=True)
    n[:, 0] = np.maximum(n[:, 0], 0)
    ok = n[:, 1] > n[:, 0]
    n = n[ok]
    n[:, 2] %= keys
    return n, ok


def landing_clashes(rows, real):
    """(start, end, key) for each landing note (rows) that sits on a real note (real) on its key: from the first
    real note's start it meets (or its own start, when one already sounds there) to the last real end it meets
    (or its own end). One row per clashing landing note; none when nothing meets. Same key, time overlapping (the
    overlap the export sees: Single channel cuts it, Multi moves a colour to another channel)."""
    empty = np.zeros((0, 3), np.int64)
    if not len(rows) or not len(real):
        return empty
    real = np.asarray(real)
    k, s, e = real[:, 2].astype(np.int64), real[:, 0].astype(np.int64), real[:, 1].astype(np.int64)
    order = np.lexsort((s, k))
    k, s, e = k[order], s[order], e[order]
    far = int(max(np.abs(s).max(), np.abs(e).max(), np.abs(rows[:, :2]).max())) + 2
    lift = np.int64(2 * far + 1)  # (each key its own stretch of numbers: searches / running ends never cross keys)
    base = k * lift + far
    place = base + s
    ends = np.maximum.accumulate(base + e)  # (latest end so far, per key: base keeps keys apart)
    rk, rs, re = rows[:, 2].astype(np.int64), rows[:, 0].astype(np.int64), rows[:, 1].astype(np.int64)
    rb = rk * lift + far
    # (latest end of the real notes on this key starting before this one ends; another key's = far below rs)
    upto = np.searchsorted(place, rb + re, "left")
    late = np.where(upto > 0, ends[np.maximum(upto - 1, 0)] - rb, rs - 1)
    hit = late > rs
    if not hit.any():
        return empty
    # (one on this key already sounding at its start = from its start; else from the first one starting after it)
    at = np.searchsorted(place, rb + rs, "left")
    before = np.where(at > 0, ends[np.maximum(at - 1, 0)] - rb, rs - 1)
    first = s[np.minimum(at, len(s) - 1)]
    lo = np.where(before > rs, rs, np.maximum(rs, first))
    hi = np.minimum(re, late)
    return np.column_stack([lo, hi, rk])[hit & (hi > lo)]


def wrapped_in(sh, notes, tracks, ppq, keys, notes_tracks):
    """sh's (notes, tracks) with its notes past the keys added back inside ("Keep notes made there" + "Wrap them
    around", user 2026-11-08): key mod the project's keys (one over the top = key 0, one under key 0 = the top key),
    cut at beat 0 like cut_back (nothing before it, a note across it starts on it). Made from the same remembered
    lifted notes as edge_made / App.offrange_tracks (engine.off_part); added before the channels / overlaps are
    worked out, so they're fixed like any other note in that mode."""
    out = past_edges(sh, keys, ppq)
    if out is None:
        return notes, tracks
    lifted, up, room, later = out
    with rows_up_to(room - 1):
        n, t = notes_tracks(lifted, room)
    if not len(n):
        return notes, tracks
    n, t = off_part(n, t, up, keys, round(later * ppq))
    n, ok = wrap_landing(n, keys)
    if not ok.any():
        return notes, tracks
    if tracks is None and t is None:
        both, tt = np.concatenate([np.asarray(notes).reshape(-1, n.shape[1]), n]), None
    else:
        a = np.zeros(len(notes), np.int64) if tracks is None else np.asarray(tracks, np.int64).ravel()
        b = np.zeros(len(n), np.int64) if t is None else np.asarray(t, np.int64)[ok]
        both, tt = np.concatenate([np.asarray(notes).reshape(-1, n.shape[1]), n]), np.concatenate([a, b])
    order = np.argsort(both[:, 0], kind="stable")
    return both[order].astype(np.asarray(notes).dtype if len(notes) else both.dtype, copy=False), \
        None if tt is None else tt[order]


def all_notes(shapes, ppq, keys, mode, split, all16, notes_tracks, quiet=False, rounds=False, order=False,
              keep=False, busy=None, own=None, stash=None, overlaps=None, wrap=False):
    """Every shape's notes together, overlaps / channels worked out: (colours each shape asks for, channel 10 used
    (slot k = channel k, slot_track_channel), rendered, slot count, {Hz bass's number: {voice: slot}} for those with
    their own channel per note (render voice_slots)). all16: the project's "Use channel 10" (every
    shape may use it, not only pictures). Touches no Tk: also made in a background thread (App.notes_rested).
    quiet: every mask's own notes left out (what the masks show: App.pattern). rounds: the project's "New channels
    each Colours round" (App.rounds_apart). order: its "Channels 1-16 in order" (App.in_order). keep: Settings →
    General "Overlapping shapes keep their colours" (render keep). busy: per shape the times its CC lines are
    sent (App.cc_busy: render busy). own: {id: channel} of the tracks added by hand. A shape laid on tracks
    (placed_on: drawn / pasted with a track highlighted) has its notes on them (colour k on the k-th, colours in
    its tracks' order; more colours than tracks: the last one's) in every mode, its own overlaps fixed as if alone
    (Single channel; As drawn: kept), other shapes' never (Domino lets two notes share a key in a track); the other
    shapes never step round them (nor take tracks added by hand). Also returns {shape's number: how many colours
    it has} for those (App.fit_into). stash: a dict given = what lay_again needs kept in it. overlaps: how overlaps
    are fixed (App.overlap_rule: Single's split around, Multi's Remove overlaps; render overlaps)."""
    team_laps = []
    across = bool(overlaps and overlaps.get("across")) and mode == "auto"  # (Multi channel's Remove overlaps)
    teams = between_turns(shapes, team_laps)
    # (each Colours shape's notes made with their rounds, then taken off the tracks: render's rounds; a merged shape's
    # parts' rounds too, merge.part_rows; an Add between step = one channel, its round = its group's: in_rounds)
    rounds = rounds and mode == "auto"
    took = [rounds and has_colours(sh) and team is None for sh, team in zip(shapes, teams)]
    wide = all16 and mode == "auto"  # (a Nested / Tiles / Ripples fill with Colours: its pairs go round 16 colours)
    shapes_as = [dict(sh, _most=ALL_COLOURS) if wide and has_pattern(sh) and sh.get("cycle") else sh for sh in shapes]
    asked = [dict(sh, _rounds=True) if r else sh for sh, r in zip(shapes_as, took)]
    got = [notes_tracks(s) for s in asked]
    if wrap:  # ("Wrap them around": the notes past the keys put back inside, App.outgoing)
        got = [wrapped_in(s, n, t, ppq, keys, notes_tracks) for s, (n, t) in zip(asked, got)]
    laps = [np.asarray(t, np.int64) // ROUND if r and t is not None else None for (_, t), r in zip(got, took)]
    got = [(n, t % ROUND) if lap is not None else (n, t) for (n, t), lap in zip(got, laps)]
    if rounds:
        laps = [np.full(len(n), tl, np.int64) if tl is not None else lap
                for (n, _), lap, tl in zip(got, laps, team_laps)]
    # (Channel per strip: each strip of a pasted track a round of its own, the first keeping its channels; strips.py)
    strips = [mode == "auto" and has_strips(sh) and t is not None for sh, (_, t) in zip(shapes, got)]
    laps = [strip_laps(n, t, gap_of(sh)) if s else lap for sh, (n, t), lap, s in zip(shapes, got, laps, strips)]
    # (pieces sliced from one paste with the same setting: their strips worked out together and given their
    # channels as one, so a strip cut in two stays one channel, hunt 2026-10-23)
    together = [(sh["paste"], gap_of(sh)) if s and isinstance(sh.get("paste"), str) else None
                for sh, s in zip(shapes, strips)]
    for key in {k for k in together if k is not None}:
        mine = [i for i, k in enumerate(together) if k == key]
        if len(mine) > 1:
            joined = strip_laps(np.concatenate([got[i][0] for i in mine]),
                                np.concatenate([np.asarray(got[i][1], np.int64) for i in mine]), key[1])
            at = np.cumsum([0] + [len(got[i][0]) for i in mine])
            for k, i in enumerate(mine):
                laps[i] = joined[at[k]:at[k + 1]]
    # (a mask whose own notes don't sound: none, mask.py)
    off = [silent(sh) or quiet and is_mask(sh) for sh in shapes]
    got = [(n[:0], None if t is None else t[:0]) if o else (n, t) for o, (n, t) in zip(off, got)]
    laps = [lap[:0] if o and lap is not None else lap for o, lap in zip(off, laps)]
    # (shapes laid on tracks: taken out of the channel work, put on their tracks below)
    into = [placed_on(sh, own) for sh in shapes]
    # ("New channels each Colours round": each round's colours are colours of their own on the tracks too, laid
    # after the round before; user 2026-10-26: a copy pasted into a track went round the same ones)
    inside = {i: (got[i][0], np.asarray(got[i][1], np.int64) + laps[i] * ROUND) if rounds and laps[i] is not None
              and got[i][1] is not None else got[i] for i, p in enumerate(into) if p}
    got = [(n[:0], None if t is None else t[:0]) if i in inside else (n, t) for i, (n, t) in enumerate(got)]
    laps = [lap[:0] if i in inside and lap is not None else lap for i, lap in enumerate(laps)]
    # a shape never has more than 15 colours (user: a MIDI player shows no more either): the extra ones are
    # merged into the last (pasted notes keep their tracks)
    # (a Hz bass layer's notes clashing with another's: another channel, not another colour asked for)
    # (a Hz bass with its own channel per note: one channel a voice, not a colour asked for)
    wanted = [0 if t is None or "notes" in sh or own_channels(sh.get("hz")) else len(np.unique(np.asarray(t) % CLASH))
              for sh, (_, t) in zip(shapes, got)]
    most = most_colours(all16)
    got = [(n, capped_colours(t % CLASH, most) + t // CLASH * CLASH if w > most else t) for (n, t), w in zip(got, wanted)]
    # (pictures using channel 10 too: one setting for the project, kept with every picture; an Apply mask copy made
    # while channel 10 was used keeps it in use, hunt)
    # (Turn into plain notes / a path's Turn into plain shapes: "solid", other shapes step round its channels)
    pins = [pinned_slots(sh, mode) and ("solid" if sh.get("made") in ("plain", "path", "leftovers") else True)
            for sh in shapes]
    rules = [slot_rule(sh) if pin else None for sh, pin in zip(shapes, pins)]
    use10 = any(r is True for r in rules) or any(shapes[i].get("slots10") is True for i in inside)
    # (pinned tracks made with channel 10 skipped stay on their channels while it's used: mask.as_used)
    got = [(n, as_used(t, r, use10 or all16)) for (n, t), r in zip(got, rules)]
    # (with all16 the shapes use channel 10 too, but only as their 16th: render's "all"; in order: "order")
    # (merged shapes and Nested / Tiles / Ripples fills take channels by colour number, not by which colour starts
    # first: a flipped or moved copy keeps its colours, user; pasted notes by track: a pasted song's channels stay
    # in their order, user 2026-10-22)
    # (pasted notes' passengers: each track's on its slot; a Domino paste's in every mode, hunt 2026-11-01)
    voice_slots = {i: None for i, sh in enumerate(shapes) if own_channels(sh.get("hz")) or "cc" in sh} \
        if mode == "auto" else {i: None for i, sh in enumerate(shapes) if "cc" in sh and pasted_notes(sh)}
    note_slots = {i: None for i, (sh, s) in enumerate(zip(shapes, strips)) if s and "cc" in sh}
    # (a sliced piece with Colours: by colour number too, so it keeps the whole's colours, user 2026-11-02: a
    # piece starting on the 3rd colour began again from the 1st)
    ordered = [bool(sh.get("merge")) or has_pattern(sh) or "notes" in sh
               or bool(sh.get("cut") or sh.get("cut_of")) and has_colours(sh, turns=True) for sh in shapes]
    # (a recoloured shape's colours: each counted as starting where it did before, so they keep their channels)
    backs = [recolour_back(s, notes_tracks, dict(s_as, _rounds=True) if r else s_as, n, t)
             if mode == "auto" and not o and t is not None and is_recoloured(s) else colours_back(s)
             for s, s_as, r, o, (n, t) in zip(shapes, shapes_as, took, ordered, got)]
    # (a one-colour piece as one track too, so its whole's pieces share it: a tiny Lasso piece's note on its
    # neighbour's pushed the pieces onto another row with keep colours, partial sweep 11-02)
    rendered, slots = render([n for n, _ in got], mode, split,
                             [np.zeros(len(n), np.int64) if t is None and mode == "auto" and len(n) and cut_group(sh)
                              else t for (n, t), sh in zip(got, shapes)],
                             [tracks_apart(sh) or is_recoloured(sh) for sh in shapes],  # (Split: from one colour)
                             pins, ("order" if order else "all") if all16 else use10, teams,
                             # (a cut shape's pieces like a paste's: one channel per colour across them, so a piece
                             # without the first colours keeps the whole's channels, partial sweep 11-02)
                             [sh.get("paste") if "notes" in sh else ("cut", cut_group(sh)) if cut_group(sh) else None
                              for sh in shapes],
                             # (a Domino paste keeps its own overlaps outside Single channel: user 2026-10-30; not
                             # with Remove overlaps ticked, user 2026-11-06)
                             [sustain_on(sh) or sh.get("held") is True or mode == "auto" and keeps_laps(sh)
                              and not across for sh in shapes],
                             backs, ordered, laps, keep,
                             [s or r and not has_colours(sh, turns=True) for sh, r, s in zip(shapes, took, strips)],
                             voice_slots,
                             busy, together, note_slots, [pasted_notes(sh) for sh in shapes],
                             # (keep colours: a cut shape's pieces move as one shape, partial sweep 11-02)
                             groups=[cut_group(sh) for sh in shapes] if keep else None,
                             overlaps=overlaps and {"way": overlaps.get("way", "stretch")})
    way = (overlaps or {}).get("way", "stretch")
    if stash is not None:  # (App.fit_into's laying again: lay_again)
        stash.update(inside=inside, rendered=rendered, slots=slots, base10=any(r is True for r in rules),
                     all16=all16, mode=mode, overlaps=overlaps if across else None, way=way)
    rendered, slots, need, laid = lay_inside(rendered, slots, inside, shapes, own, use10 or all16, mode, way)
    emptied = frozenset()
    if across and len(rendered):  # (Remove overlaps: after the laid shapes are in, so your tracks take part too)
        rendered, emptied = cut_across(rendered, shapes, overlaps)
    voice_slots.update(laid)
    for i, s in note_slots.items():  # (Channel per strip with CCs: each strip's slot + time, App.cc_messages)
        if s is not None and len(s):
            voice_slots[("strips", i)] = strip_spans(got[i][0], got[i][1], s)
    if stash is not None:
        stash["voice_slots"] = voice_slots
    return (wanted, use10 or all16, rendered, slots, {i: s for i, s in voice_slots.items() if s is not None}, need,
            emptied)


def cut_across(rendered, shapes, overlaps):
    """all_notes' Remove overlaps step (engine.remove_across) -> (notes, the automatic tracks it left without notes).
    The order: the track list's as it is BEFORE the cut (user 2026-11-06, hunt: an emptied track no longer counted for
    your tracks' places, so yours moved down and the list no longer showed who won); those emptied tracks keep
    counting as tracks with notes everywhere (App.cut_emptied: list, exports), so nothing moves. The loaded MIDI's
    tracks first, in their listed order (overlaps "files")."""
    held = np.flatnonzero([sustain_on(sh) or sh.get("held") is True for sh in shapes])
    before = np.unique(rendered[:, 4])
    files = overlaps.get("files") or ()
    out = remove_across(rendered, np.isin(rendered[:, 5], held), overlaps,
                        lambda n: track_ranks(n[:, 4], overlaps.get("own") or (), files=files)[0])
    gone = np.setdiff1d(before, np.unique(out[:, 4]) if len(out) else before[:0])
    return out, frozenset(int(s) for s in gone.tolist() if s < OWN0)


def lay_inside(rendered, slots, inside, shapes, own, use10, mode, way="stretch"):
    """all_notes' last part: the shapes laid on tracks ({number: (notes, colours)}) put on them, after the other
    shapes' rendered notes: (rendered, slot count, {number: how many colours it has}, {number: {pasted track: slot}}
    for laid pasted notes with CC events: render's voice_slots, Multi channel only, App.cc_messages)."""
    need, laid = {}, {}
    if not inside:
        return rendered, slots, need, laid
    parts, together = [rendered], []
    for i, (n, t) in inside.items():
        sh = shapes[i]
        colour = (np.unique(np.asarray(t, np.int64), return_inverse=True)[1].ravel() if t is not None and len(n)
                  else np.zeros(len(n), np.int64))
        need[i] = int(colour.max()) + 1 if len(n) else 0
        ids = placed_on(sh, own)
        if not ids:
            continue
        on = np.array(laid_slots_of(sh, ids, own, use10), np.int64)
        # (Single channel / As drawn: every colour on its first track, hunt 2026-10-26; an Extract keeps its channels,
        # pasted notes their tracks: user 2026-10-29, "you copy from Domino is what you'll get")
        kept = colours_apart(sh, mode)
        if not kept:
            colour = np.zeros_like(colour)
        if kept and "cc" in sh and t is not None and len(n):  # (each pasted track's events: its colour's)
            tracks = np.unique(np.asarray(t, np.int64))
            laid[i] = {int(u): int(on[min(k, len(ids) - 1)]) for k, u in enumerate(tracks.tolist())}
        # (a Domino paste keeps its own overlaps in Multi channel too, as copied: user 2026-10-30, round trip)
        fix = mode != "raw" and not sustain_on(sh) and sh.get("held") is not True and not (
            mode == "auto" and keeps_laps(sh))
        if mode == "single" and fix:  # (Single channel: no overlaps between the shapes on a track either, user
            together.append(in_track(n, on[np.minimum(colour, len(ids) - 1)], i, False))  # 2026-10-26)
        else:
            parts.append(in_track(n, on[np.minimum(colour, len(ids) - 1)], i, fix, way))
    if together:  # (pinned on one of Spiderweb's tracks: with the notes already there)
        together = np.concatenate(together)
        there = np.isin(rendered[:, 4], np.unique(together[together[:, 4] < OWN0, 4]))
        if there.any():  # (sustained / held shapes and pictures keep their overlaps, as in render: hunt 10-26)
            keeps = np.array([sustain_on(s) or s.get("held") is True or picture_like(s) for s in shapes] + [True])
            there &= ~keeps[np.minimum(rendered[:, 5], len(shapes))]
        if there.any():
            parts[0] = rendered[~there]
            together = np.concatenate([rendered[there], together])
        parts.append(fix_rows(together, way))
    rendered = np.concatenate(parts)
    return rendered, max(slots, auto_count(rendered[:, 4])), need, laid  # (automatic tracks shapes were pinned on, B11)


def lay_again(made, stash, shapes, own):
    """all_notes' answer again after App.fit_into changed only where laid shapes go (stash: what all_notes kept),
    without making every shape's notes again (hunt 2026-10-26: a paste into a track made the whole song twice); None
    when that isn't enough (a laid shape left / joined the others, or channel 10's use changed: all_notes again)."""
    inside = stash.get("inside")
    if inside is None or {i for i, sh in enumerate(shapes) if placed_on(sh, own)} != set(inside):
        return None
    use10 = stash["base10"] or any(shapes[i].get("slots10") is True for i in inside)
    if (use10 or stash["all16"]) != made[1]:
        return None
    rendered, slots, need, laid = lay_inside(stash["rendered"], stash["slots"], inside, shapes, own, made[1],
                                             stash["mode"], stash.get("way", "stretch"))
    emptied = frozenset()
    if stash.get("overlaps") and len(rendered):  # (Remove overlaps after laying again too: hunt 2026-11-06, the
        rendered, emptied = cut_across(rendered, shapes, stash["overlaps"])  # overlaps stayed till the next edit)
    return made[0], made[1], rendered, slots, {**made[4], **laid}, need, emptied


def in_track(notes, slots, owner, fix, way="stretch"):
    """A shape's notes on tracks added by hand (slots: each note's): rendered rows (fix: its own overlaps on each
    track fixed, fix_rows)."""
    rows = np.zeros((len(notes), 6), np.int64)
    rows[:, :4], rows[:, 5], rows[:, 4] = notes, owner, slots
    return fix_rows(rows, way) if fix else rows


def as_heard(sh, mode, rule):
    """sh as its notes are made in this Channels mode: a notes shape whose own overlaps the mode fixes carries the
    way ("_heard", engine.heard_way / heard_notes), so it's made, kept and looked up apart from the other modes."""
    way = heard_way(sh, mode, rule)
    return dict(sh, _heard=way) if way else sh


def fix_rows(rows, way="stretch"):
    """Rendered rows with their overlaps on each track fixed: cut + stretch (resolve_overlaps), or split around
    (way "split": Single's tick / Multi's Remove overlaps, hunt 2026-11-06: laid shapes were always stretched)."""
    if not len(rows):
        return rows
    rows = rows.copy()
    ids, rows[:, 4] = np.unique(rows[:, 4], return_inverse=True)  # (small numbers while fixing: OWN0's are huge)
    rows = split_overlaps(rows) if way == "split" else resolve_overlaps(rows)
    rows[:, 4] = ids[rows[:, 4]]
    return rows


def recolour_back(sh, notes_tracks, made_as, notes, tracks):
    """render's back for a recoloured shape: each note's start counted as its colour's first start before Recolour
    (unflipped when gate flipped), so Amount 0 gives the channels Off does (recolour.py)."""
    bn, bt = notes_tracks(without_recolour(made_as))
    if bt is None or not len(bn):
        return colours_back(sh)
    back = colours_back(sh)  # (new colours, Split's too: counted by where they start unflipped)
    return counted_starts(notes, tracks, first_starts(bn, bt, unflipped_starts(bn) if back else None),
                          unflipped_starts(notes) if back else None)


def sends_cc(sh):
    """A Hz bass shape sending CC lines."""
    return sh.get("kind") is not None and has_cc(sh.get("hz") or {})


def cc_busy(shapes, ppq, mode):
    """all_notes' busy: per shape None, or {track: (start, end)} the times its CC lines are sent (hz_cc.cc_spans at the
    Hz bass's own BPM, as its notes): Multi channel keeps its channel for them, on every key, so no other shape
    takes it while its lines (run-on too) change it (user 2026-10-21: either split). None otherwise."""
    if mode != "auto":  # (a Domino paste's tracks: as in Multi channel in every mode, hunt 2026-11-01)
        if not any("cc" in sh and pasted_notes(sh) for sh in shapes):
            return None
        return [passenger_spans(sh, ppq) or None if "cc" in sh and pasted_notes(sh) else None for sh in shapes]
    if not any(sends_cc(sh) or "cc" in sh for sh in shapes):
        return None
    # (pasted notes' CCs too: each track's channel held from its first event to its last, hunt 2026-10-22: a volume
    # change after a track's last note changed the shape that took its channel next)
    return [cc_spans(sh, ppq, float(sh["hz"]["bpm"])) or None if sends_cc(sh) else
            passenger_spans(sh, ppq) or None if "cc" in sh else None for sh in shapes]


def cc_texts(shapes, mode):
    """What cc_busy comes from, for App.notes_key (cheap: the times are worked out in the background): Auto's run-on
    and each CC Hz bass's whole text (shape_key leaves its lines out), None when cc_busy gives None."""
    if mode != "auto" or not any(sends_cc(sh) for sh in shapes):
        return None
    return (hz_cc.auto_ring, tuple(json.dumps(dict(sh, hz={k: v for k, v in sh["hz"].items() if k != "_memo"}),
                                              sort_keys=True, default=repr) if sends_cc(sh) else None
                                   for sh in shapes))
