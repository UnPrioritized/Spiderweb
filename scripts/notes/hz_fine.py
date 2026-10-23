"""A Hz bass on the file's finer ticks inside Keep timing parts (hz["fine"], "Finer gates in Keep timing parts" in
the Hz window's Settings…; user 2026-10-23, after a hearing test: 120 -> 1000 BPM, the high notes lost their buzz).

Inside a Keep timing part drawn faster than the tempo heard before it, the MIDI file has more ticks a second than
Spiderweb's own. The other notes lay the part's file ticks out as always (anchors: tempo_file.file_plan); this Hz
bass's notes there take no part in that: it's made again on a grid as fine as the file's ticks there and each note
goes on the file tick nearest its real time (the export only; the piano roll and playback stay on Spiderweb's
ticks). Each note keeps its track (layer clashes, own channel per note voices, Colours turns) and goes on the
channel the piano roll gives that track; overlaps on a key are fixed as the Channels mode fixes them (hunt
2026-10-23: matching by key alone mixed layers / voices / colours up)."""

import numpy as np

from notes.engine import resolve_overlaps
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


def track_slots(made, tracks, own):
    """{track: slot}: the slot most of each track's notes have on the piano roll. made / tracks = the shape's notes
    as made (shape_notes_tracks), own = its rendered rows (notes on one key starting together: the same slot
    picked for the track they come from most often)."""
    if tracks is None or not len(made) or not len(own):
        return {}
    key = lambda s, p: s.astype(np.int64) * 512 + p
    have = key(own[:, 0], own[:, 2])
    order = np.argsort(have, kind="stable")
    have = have[order]
    want = key(made[:, 0], made[:, 2])
    j = np.clip(np.searchsorted(have, want), 0, len(have) - 1)
    hit = have[j] == want
    pairs = np.column_stack([np.asarray(tracks, np.int64)[hit], own[order[j[hit]], 4]])
    if not len(pairs):
        return {}
    got, counts = np.unique(pairs, axis=0, return_counts=True)
    out = {}
    for (t, s), c in zip(got.tolist(), counts.tolist()):
        if c > out.get(t, (None, -1))[1]:
            out[t] = (s, c)
    return {t: s for t, (s, _) in out.items()}


def finer_notes(owners, notes, moved, parts, tempo, base, ppq, events, made, mode):
    """moved (= notes on the file's ticks) with the finer Hz basses' notes inside the parts made again on the
    file's ticks: events = the file's tempo [(tick, us)] (file_plan's, worked out without them); made(i, ppq) =
    shape i's (notes, tracks) as the piano roll makes them, at that PPQ; mode = the Channels mode."""
    drop = swapped(notes, owners, parts)
    keep = moved[~drop]
    end = int(notes[:, 1].max()) + 1 if len(notes) else 1
    heard = heard_tempo(tempo, base, ppq, end) or [(0, round(60_000_000 / base))]
    out = [keep]
    for i in owners:
        own = notes[notes[:, 5] == i]
        if not len(own):
            continue
        times = max([t for a, e, t in parts if (own[:, 0] < e).any() and (own[:, 1] > a).any()] or [0])
        if times < 2:
            continue
        fine, tracks = made(i, ppq * times)
        if not len(fine):
            continue
        at = fine[:, 0] / times  # (Spiderweb's ticks, fractional)
        inside = in_parts(at, parts)
        if not inside.any():
            continue
        slots = track_slots(*made(i, ppq), own)
        vals, counts = np.unique(own[:, 4], return_counts=True)
        most = int(vals[np.argmax(counts)])  # (a track the piano roll has no note of: the slot most notes are on)
        if tracks is None:
            slot = np.full(int(inside.sum()), most, np.int64)
        else:
            slot = np.array([slots.get(t, most) for t in np.asarray(tracks, np.int64)[inside].tolist()], np.int64)
        fine, at = fine[inside], at[inside]
        t0 = np.rint(ticks_at(events, ppq, seconds_at(heard, ppq, at))).astype(np.int64)
        t1 = np.rint(ticks_at(events, ppq, seconds_at(heard, ppq, fine[:, 1] / times))).astype(np.int64)
        rows = np.column_stack([t0, np.maximum(t1, t0 + 1), fine[:, 2], fine[:, 3], slot,
                                np.full(len(fine), i)]).astype(np.int64)
        mine = np.concatenate([keep[keep[:, 5] == i], rows])
        out[0] = out[0][out[0][:, 5] != i]
        # (overlaps on a key fixed as the piano roll fixes them: As drawn keeps them)
        out.append(mine if mode == "raw" else resolve_overlaps(mine))
    out = np.concatenate(out)
    return out[np.lexsort((out[:, 2], out[:, 0]))]
