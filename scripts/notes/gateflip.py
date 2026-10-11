"""Gate aware flip (right-click, above the normal flips): only how a spam shape's gates are laid is flipped sideways,
not the drawing. sh["gate_flip"] = True.

The notes are made from the drawing flipped sideways (its Range graph NOT reversed, its velocities as they are), then
flipped back onto the real drawing: each key row is laid from its right edge, an exact mirror image of the shape
laid from its left (Colours, area colours, outline gate band and leftover slivers go along). Not the same as
reversing the Range graph: that still lays every row from its left edge (user's pictures, 2026-10-11). With Multi
channel its colours are handed out in mirrored order too (engine.render back: the turn at the right edge gets the
channel the one at the left edge had).
Done before glue, note tool pages and Sustain. Switching it on / off turns the velocities round once, like a flip
(drawing them again in the velocity pane overrides that as usual)."""

import json
import math

import numpy as np

from notes.custom import SPAM_FILLS, gate_ticks
from notes.fx import flip_shape, reverse_velocity
from notes.sustain import gap_of, row_order

LEFT_OUT = ("cut", "merge", "notes", "picture", "hz")  # (sliced pieces / merged: a later step; pasted: their own way)


def offered(sh):
    """True if the menu item is shown for sh: no Hz bass, funnel or image (user)."""
    return sh["kind"] != "funnel" and not sh.get("hz") and "picture" not in sh


def is_pasted(sh):
    """True if sh is pasted notes the gate aware flip works on (each run on a key mirrored inside itself)."""
    return sh["kind"] == "custom" and "notes" in sh and not any(k in sh for k in ("cut", "merge", "picture", "hz"))


def why_not(sh, notes, tracks=None):
    """None if a gate aware flip changes sh's notes (notes: (start, end, key, velocity) rows, tracks: their
    colours), else why not: "one_gate" (every key row has one note, or pasted notes whose runs read the same both
    ways, colours included: nothing to lay the other way, user) or "no_gates"."""
    if is_pasted(sh):
        return "one_gate" if runs_same_both_ways(notes, tracks) else None
    if sh["kind"] != "custom" or sh.get("fill") not in SPAM_FILLS or any(k in sh for k in LEFT_OUT):
        return "no_gates"
    if len(np.unique(notes[:, 2])) == len(notes):
        return "one_gate"
    return None


def is_flipped(sh):
    """True if sh's notes are made gate flipped from its drawing (pasted notes: is_pasted_flipped)."""
    return (sh.get("gate_flip") is True and sh["kind"] == "custom" and sh.get("fill") in SPAM_FILLS
            and not any(k in sh for k in LEFT_OUT))


def is_pasted_flipped(sh):
    return sh.get("gate_flip") is True and is_pasted(sh)


def mirrored_runs(notes, tracks, carry_vel):
    """Pasted notes' gates laid the other way: on each key, each run (notes whose gaps are no longer than the usual
    gap, as Sustain's Auto works it out) mirrored inside itself, its first start and last end kept (user). Colours
    (tracks) go with their notes; velocities too when they're the notes' own (carry_vel), else each place keeps its
    velocity (a velocity line drawn in Spiderweb: turned round once when switched on, like a flip)."""
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


def mirror_ticks(sh, right, ppq):
    """Twice the tick the drawing is flipped around (right = its right end in beats): a whole bar line (4 beats) at
    or past twice its right end, so the flipped drawing never starts before the song does and the ticks flip back
    exactly. Start Aligned with no Range: a line of its gate grid too (counted from tick 0), so the notes stay on
    it (only the colours' order turns round, user)."""
    step = 4 * ppq
    if sh.get("align") == "aligned" and not sh.get("range"):
        step = math.lcm(step, gate_ticks(sh["gate"], ppq))
    return step * max(1, math.ceil(2 * right * ppq / step))


def flipped_drawing(sh, mid2):
    """sh's drawing flipped sideways around mid2 / 2 for making its notes: no glue, pages or Sustain (done after, on
    the real shape), the Range as it was (laid the same way, from the left), velocities as they were."""
    out = json.loads(json.dumps({k: v for k, v in sh.items()
                                 if k not in ("gate_flip", "glue", "fx", "_m", "sustain")}))
    flip_shape(out, True, mid2)  # (its velocities turned round too: flipped back with the notes, they're as drawn)
    for k in ("range", "range_kept"):
        if k in sh:
            out[k] = json.loads(json.dumps(sh[k]))
    return out


def flipped_back(notes, ticks):
    """Notes made from the flipped drawing put back: start / end mirrored around ticks / 2."""
    out = notes.copy()
    out[:, 0], out[:, 1] = ticks - notes[:, 1], ticks - notes[:, 0]
    return out


def runs(notes):
    """(each note's run number, each run's key, first start, place among its key's runs) of (start, end, key, ...)
    rows: a run = notes on one key touching or overlapping one another."""
    if not len(notes):
        z = np.zeros(0, np.int64)
        return z, z, z, z
    o = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, k = notes[o, 0], notes[o, 1], notes[o, 2]
    big = int(e.max()) + 2  # (the latest end so far on the same key: keys apart by big)
    reach = np.maximum.accumulate(e + (k - k.min()) * big) - (k - k.min()) * big
    new = np.r_[True, (k[1:] != k[:-1]) | (s[1:] > reach[:-1])]
    num = np.cumsum(new) - 1
    out = np.empty(len(notes), np.int64)
    out[o] = num
    first = np.flatnonzero(new)
    rk = k[first]
    key_first = np.r_[True, rk[1:] != rk[:-1]]
    place = np.arange(len(first)) - np.maximum.accumulate(np.where(key_first, np.arange(len(first)), 0))
    return out, rk, s[first], place


def kept_in_place(notes, plain):
    """The gate flipped notes with each run moved to start where the same run starts unflipped (the leftover bit a
    row's rounded last gate leaves stays where it was, user: nothing shifts). Keys whose runs don't pair up: as
    they are."""
    if not len(notes) or not len(plain):
        return notes
    num, k, s, place = runs(notes)
    _, pk, ps, pplace = runs(plain)
    keys, counts = np.unique(k, return_counts=True)
    pkeys, pcounts = np.unique(pk, return_counts=True)
    at = np.clip(np.searchsorted(pkeys, k), 0, len(pkeys) - 1)
    same_count = (pkeys[at] == k) & (pcounts[at] == counts[np.searchsorted(keys, k)])
    span = int(max(place.max(), pplace.max())) + 1
    ids, pids = k * span + place, pk * span + pplace  # (both in order: by key, then start)
    j = np.clip(np.searchsorted(pids, ids), 0, len(pids) - 1)
    ok = same_count & (pids[j] == ids)
    shift = np.where(ok, ps[j] - s, 0)
    if not shift.any():
        return notes
    out = notes.copy()
    out[:, 0] += shift[num]
    out[:, 1] += shift[num]
    return out


def turn_velocities(sh):
    """The velocities turned round, as a sideways flip does (switching the gate aware flip on / off): the line, and
    velocities drawn after its note tool pages."""
    reverse_velocity(sh)
    for step in sh.get("fx") or ():
        if step["tool"] == "vel":
            step["pts"] = [[1 - u, v] for u, v in reversed(step["pts"])]
