"""Gate aware flip (right-click, above the normal flips): only how a spam shape's gates are laid is flipped sideways,
not the drawing. sh["gate_flip"] = True.

The notes are made as usual, then each run on each key row (mirrored_runs) is mirrored inside itself: every key row
reads exactly reversed, gates, colours and all (user 2026-10-11: r g b w -> w b g r, no exception), its first start
and last end kept. Not the same as reversing the Range graph: that still lays every row from its left edge (user's
pictures). With Multi channel each colour keeps the channel it has unflipped (engine.render back: channels handed
out by where each colour starts unflipped, unflipped_starts).
Done before glue, note tool pages and Sustain. Switching it on / off turns the velocities round once, like a flip
(drawing them again in the velocity pane overrides that as usual)."""

import numpy as np

from notes.custom import SPAM_FILLS
from notes.fx import reverse_velocity
from notes.sustain import gap_of, row_order

NOT_DRAWN = ("merge", "notes", "picture", "hz")  # (pasted notes and merged shapes: each run mirrored, is_pasted)
LEFT_OUT = ("cut",) + NOT_DRAWN  # (a sliced piece: its whole's notes are made with the piece's setting, sliced.source)


def offered(sh):
    """True if the menu item is shown for sh: no Hz bass, funnel or image (user)."""
    return sh["kind"] != "funnel" and not sh.get("hz") and "picture" not in sh


def is_pasted(sh):
    """True if sh is pasted notes or a merged shape (Gate sensitive merge: the whole of each key row across both
    parts, user 2026-10-11) the gate aware flip works on: each run on a key mirrored inside itself."""
    return sh["kind"] == "custom" and "notes" in sh and not any(k in sh for k in ("cut", "picture", "hz"))


def why_not(sh, notes, tracks=None):
    """None if a gate aware flip changes sh's notes (notes: (start, end, key, velocity) rows, tracks: their
    colours), else why not: "one_gate" (every key row has one note, or pasted notes whose runs read the same both
    ways, colours included: nothing to lay the other way, user) or "no_gates"."""
    if is_pasted(sh):
        return "one_gate" if runs_same_both_ways(notes, tracks) else None
    if sh["kind"] != "custom" or sh.get("fill") not in SPAM_FILLS or any(k in sh for k in NOT_DRAWN):
        return "no_gates"
    if len(np.unique(notes[:, 2])) == len(notes):
        return "one_gate"
    return None


def all_flipped(sh):
    """True if sh is gate flipped: the menu's tick and the Shapes list's "(Gates flipped)" (one rule for both, hunt)."""
    return sh.get("gate_flip") is True and sh["kind"] == "custom" and (
        is_pasted(sh) or sh.get("fill") in SPAM_FILLS and not any(k in sh for k in NOT_DRAWN))


def lifted(parts):
    """A merge's parts all gate flipped: their flips taken off (velocities turned back) so the merged shape is
    flipped as a whole instead, each row across both parts (user 2026-10-11: it should work all the way across).
    Changes parts; returns [was flipped] per part (Split puts them back: put_back), or None when they weren't all
    flipped (one flipped part keeps its own)."""
    if not parts or not all(p.get("gate_flip") is True for p in parts):
        return None
    for p in parts:
        del p["gate_flip"]
        if p.get("fx"):  # (its own steps: turn_velocities changes them in place)
            p["fx"] = [dict(s) for s in p["fx"]]
        turn_velocities(p)
    return [True] * len(parts)


def put_back(parts, flips):
    """Split: the flips lifted when merged (lifted) back on the parts."""
    for p, f in zip(parts, flips or ()):
        if f and p.get("gate_flip") is not True:
            p["gate_flip"] = True
            turn_velocities(p)


def colours_back(sh):
    """True if sh is gate flipped, its colours given the channels they have unflipped (engine.render back). Pasted
    notes sliced from a flipped paste ("flip_colours", slice.slice_notes): "whole" = flipped over the whole paste,
    its pieces counted together (their runs went across the cuts); flipped again they're as pasted."""
    if is_pasted(sh):
        if sh.get("flip_colours") is True:
            return "whole" if sh.get("gate_flip") is not True else False
        return sh.get("gate_flip") is True
    return all_flipped(sh)


def is_flipped(sh):
    """True if sh is a drawn shape whose notes are gate flipped (pasted notes: is_pasted_flipped)."""
    return (sh.get("gate_flip") is True and sh["kind"] == "custom" and sh.get("fill") in SPAM_FILLS
            and not any(k in sh for k in LEFT_OUT))


def is_pasted_flipped(sh):
    return sh.get("gate_flip") is True and is_pasted(sh)


def unflipped_starts(notes):
    """Where each of these gate flipped notes starts unflipped (mirroring the runs again puts them back)."""
    if len(notes) < 2:
        return notes[:, 0]
    order, _, out, _ = _mirrored(notes)
    back = np.empty(len(notes), np.int64)
    back[order] = out[:, 0]
    return back


def mirrored_runs(notes, tracks, carry_vel):
    """Gates laid the other way: on each key, each run (notes whose gaps are no longer than the usual gap, as
    Sustain's Auto works it out; a spam shape's touching notes) mirrored inside itself, its first start and last end
    kept (user). Colours (tracks) go with their notes; velocities too when they're the notes' own (carry_vel), else
    each place keeps its velocity (a velocity line drawn in Spiderweb: turned round once when switched on, like a
    flip)."""
    if len(notes) < 2:
        return notes, tracks
    order, a, out, other = _mirrored(notes)
    if not carry_vel and a.shape[1] > 3:  # (the note at the other end of its run had this place's velocity)
        out[:, 3] = a[other, 3]
    back = np.empty_like(notes)
    back[order] = out
    return back, tracks


def _mirrored(notes):
    """mirrored_runs' work: (the order, the notes in it, them mirrored, each one's partner at the other end of its
    run)."""
    order, gaps = row_order(notes)
    new = (gaps < 0) | (gaps > gap_of(gaps))
    first = np.flatnonzero(new)
    run = np.cumsum(new) - 1
    a = notes[order]
    lo = np.minimum.reduceat(a[:, 0], first)[run]
    hi = np.maximum.reduceat(a[:, 1], first)[run]
    out = a.copy()
    out[:, 0], out[:, 1] = lo + hi - a[:, 1], lo + hi - a[:, 0]
    last = np.r_[first[1:], len(a)] - 1
    return order, a, out, first[run] + last[run] - np.arange(len(a))


def runs_same_both_ways(notes, tracks=None):
    """True if mirroring every run changes nothing: each note matches its partner at the other end of its run, laid
    the other way, colour (track) too (quick: a right-click on millions of pasted notes asks it)."""
    if len(notes) < 2:
        return True
    if _same[0] is not notes or _same[1] is not tracks:  # (the app hands over the same remembered arrays each time)
        a = notes[:, :4]
        if tracks is not None:
            a = np.column_stack([a, np.asarray(tracks, np.int64)])
        _, a, out, other = _mirrored(a)
        _same[:] = [notes, tracks, bool((out[other] == a).all())]
    return _same[2]


_same = [None, None, False]  # (the notes and tracks last asked about, and the answer)


def turn_velocities(sh):
    """The velocities turned round, as a sideways flip does (switching the gate aware flip on / off): the line, and
    velocities drawn after its note tool pages."""
    reverse_velocity(sh)
    for step in sh.get("fx") or ():
        if step["tool"] == "vel":
            step["pts"] = [[1 - u, v] for u, v in reversed(step["pts"])]
