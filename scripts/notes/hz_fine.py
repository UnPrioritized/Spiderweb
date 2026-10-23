"""A Hz bass on the file's finer ticks inside Keep timing parts (hz["fine"], "Finer gates in Keep timing parts" in
the Hz window's Settings…; user 2026-10-23, after a hearing test: 120 -> 1000 BPM, the high notes lost their buzz).

Inside a Keep timing part drawn faster than the tempo heard before it, the MIDI file has more ticks a second than
Spiderweb's own. The other notes lay the part's file ticks out as always (anchors: tempo_file.file_plan); this Hz
bass's notes there take no part in that: it's made again on a grid as fine as the file's ticks there and each note
goes on the file tick nearest its real time (the export only; the piano roll and playback stay on Spiderweb's
ticks). Its channels are the ones its notes have on the piano roll (the note on that key sounding then)."""

import numpy as np

from notes.engine import shape_notes
from notes.tempo_file import heard_tempo, seconds_at, ticks_at


def fine_owners(shapes):
    """The numbers of the Hz basses with finer gates ticked."""
    return [i for i, sh in enumerate(shapes) if (sh.get("hz") or {}).get("fine") is True]


def in_parts(ticks, parts):
    """True for each tick (Spiderweb's, may be fractional) inside one of the parts [(a, e, times)]."""
    t = np.asarray(ticks)
    inside = np.zeros(t.shape, bool)
    for a, e, _ in parts:
        inside |= (t >= a) & (t < e)
    return inside


def swapped(notes, owners, parts):
    """True for each note (Spiderweb's ticks) that the finer version replaces: a finer Hz bass's, starting inside a
    part."""
    return np.isin(notes[:, 5], owners) & in_parts(notes[:, 0], parts)


def finer_notes(shapes, owners, notes, moved, parts, tempo, base, ppq, keys, events):
    """moved (= notes on the file's ticks) with the finer Hz basses' notes inside the parts made again on the
    file's ticks: events = the file's tempo [(tick, us)] (file_plan's, worked out without them)."""
    drop = swapped(notes, owners, parts)
    keep = moved[~drop]
    end = int(notes[:, 1].max()) + 1 if len(notes) else 1
    heard = heard_tempo(tempo, base, ppq, end) or [(0, round(60_000_000 / base))]
    made = []
    for i in owners:
        own = notes[notes[:, 5] == i]
        if not len(own):
            continue
        times = max([t for a, e, t in parts if (own[:, 0] < e).any() and (own[:, 1] > a).any()] or [0])
        if times < 2:
            continue
        fine = shape_notes(shapes[i], ppq * times, keys)
        if not len(fine):
            continue
        at = fine[:, 0] / times  # (Spiderweb's ticks, fractional)
        inside = in_parts(at, parts)
        if not inside.any():
            continue
        fine, at = fine[inside], at[inside]
        t0 = np.rint(ticks_at(events, ppq, seconds_at(heard, ppq, at))).astype(np.int64)
        t1 = np.rint(ticks_at(events, ppq, seconds_at(heard, ppq, fine[:, 1] / times))).astype(np.int64)
        t1 = np.maximum(t1, t0 + 1)
        # the channel its notes have on the piano roll: the note on that key that started last before it
        order = np.lexsort((own[:, 0], own[:, 2]))
        keys_at = own[order, 2].astype(np.float64) * (end + 2) + own[order, 0]
        j = np.searchsorted(keys_at, fine[:, 2] * (end + 2.0) + at, "right") - 1
        j = np.clip(j, 0, len(order) - 1)
        miss = own[order[j], 2] != fine[:, 2]  # (no note on that key before it: the first after it)
        j2 = np.clip(j + 1, 0, len(order) - 1)
        j = np.where(miss & (own[order[j2], 2] == fine[:, 2]), j2, j)
        slots = own[order[j], 4]
        miss = own[order[j], 2] != fine[:, 2]
        if miss.any():  # (no note on that key at all: the slot most of its notes are on)
            vals, counts = np.unique(own[:, 4], return_counts=True)
            slots = np.where(miss, vals[np.argmax(counts)], slots)
        made.append(np.column_stack([t0, t1, fine[:, 2], fine[:, 3], slots, np.full(len(fine), i)]).astype(
            np.int64))
    if not made:
        return keep
    out = np.concatenate([keep] + made)
    # a finer bass's notes on one key + channel never overlap (one note to the next start; the same start: one)
    fin = np.isin(out[:, 5], owners)
    part = out[fin]
    order = np.lexsort((-part[:, 1], part[:, 0], part[:, 2], part[:, 4]))
    part = part[order]
    same = (part[1:, 4] == part[:-1, 4]) & (part[1:, 2] == part[:-1, 2])
    dup = np.concatenate([[False], same & (part[1:, 0] == part[:-1, 0])])
    part = part[~dup]
    same = (part[1:, 4] == part[:-1, 4]) & (part[1:, 2] == part[:-1, 2])
    nxt = np.where(same, part[1:, 0], np.iinfo(np.int64).max)
    part[:-1, 1] = np.minimum(part[:-1, 1], nxt)
    out = np.concatenate([out[~fin], part])
    return out[np.lexsort((out[:, 2], out[:, 0]))]
