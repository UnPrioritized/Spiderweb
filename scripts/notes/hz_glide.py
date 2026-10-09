"""Hz bass slides and legato: which notes slide or glide into which, the chains of notes joined that
way, and each note's own time span."""

import bisect

import numpy as np

from notes.hz_settings import MIN_LEN


def cached(hz, key, make):
    """make() worked out once for this hz while its notes are being made (hz["_memo"]: only on the private copies
    the note making works on; without it, every time)."""
    memo = hz.get("_memo")
    if memo is None:
        return make()
    if key not in memo:
        memo[key] = make()
    return memo[key]


def chains(tones, joined=()):
    """{tone id: (beat its chain of slides starts at, beat the chain ends at)}: a tone reached by a slide belongs to
    the chain of the (earliest) tone sliding into it; joined = more (a, b) pairs counted like slides (Legato:
    legato_links)."""
    by = {n["id"]: n for n in tones}
    came = {}
    for a, b in [(a, b) for a, b, _ in links(tones)] + list(joined):
        if b["id"] not in came or a["t"] < came[b["id"]]["t"]:
            came[b["id"]] = a
    heads = {}

    def head(n):
        seen = set()
        while n["id"] in came and n["id"] not in seen:  # (slides only go forward in time: no loops, but be safe)
            seen.add(n["id"])
            n = came[n["id"]]
        return n

    for n in tones:
        heads[n["id"]] = head(n)
    ends = {}
    for n in tones:
        h = heads[n["id"]]["id"]
        ends[h] = max(ends.get(h, 0.0), n["t"] + n["len"])
    return {i: (by[h["id"]]["t"], ends[h["id"]]) for i, h in heads.items()}


def note_span(hz, beat, tone=None):
    """(start, end) beats of the note (chain of slides) an effect counted from each note is in at beat (an array,
    from the shape's left edge): tone's chain, or (tone None) the latest chain to start (chains starting together:
    the longest). None when there are no tones."""
    tones = hz.get("tones") or ()
    got = cached(hz, "chains", lambda: chains(tones, legato_links(hz.get("voice"), tones)))
    if not got:
        return None
    if tone is not None and tone.get("id") in got:
        return got[tone["id"]]
    spans = {}
    for s, e in got.values():
        spans[s] = max(spans.get(s, e), e)
    starts = np.array(sorted(spans))
    ends = np.array([spans[s] for s in starts])
    i = np.clip(np.searchsorted(starts, beat, side="right") - 1, 0, len(starts) - 1)
    return starts[i], ends[i]


def note_beats(hz, name, beat, tone=None):
    """Beats into an effect counted from each note (hz["from"]) at beat (an array, from the shape's left edge):
    from the start of its note (note_span); stretched so a chain lasts one repeat when the effect is in hz["fit"]."""
    beat = np.asarray(beat, float)
    span = note_span(hz, beat, tone)
    if span is None:
        return beat
    s0, end = span
    u = np.maximum(0.0, beat - s0)
    if name in (hz.get("fit") or ()):
        u = u * hz["loop"][name] / np.maximum(end - s0, MIN_LEN)
    return u


def tones_span(tones):
    """How long the tones are together, in beats."""
    return max((n["t"] + n["len"] for n in tones), default=0.0)


def next_id(tones):
    """The number for a new tone."""
    return max((n["id"] for n in tones), default=0) + 1


def pitch(n):
    """A placed tone's pitch in keys: its key moved by its own tune (n["cents"])."""
    return n["key"] + n.get("cents", 0.0) / 100.0


def can_slide(a, b):
    """True when tone a can slide to tone b: b starts at or after a's end."""
    return a is not b and b["t"] >= a["t"] + a["len"] - 1e-9


def links(tones):
    """[(a, b, s)]: the slides that count, tone a to tone b; s = the slide itself (in a["to"]: its leads). A slide
    to a tone that starts before a's end (it was moved there) is kept but does nothing."""
    by = {n["id"]: n for n in tones}
    out = []
    for a in tones:
        for s in a["to"]:
            b = by.get(s["id"])
            if b is not None and can_slide(a, b):
                out.append((a, b, s))
    return out


def glide(a, b, s):
    """A slide as (start beat, end beat, pitch at the start, pitch at the end): it leaves a's tone `out` before
    a's end and reaches b's tone `in` after b's start."""
    return a["t"] + a["len"] - min(s["out"], a["len"]), b["t"] + min(s["in"], b["len"]), pitch(a), pitch(b)


def legato_links(voice, tones):
    """[(a, b)] with Legato on (hz["voice"]["legato"], the Voice box): tone b starts right where tone a ends, so b
    carries on a's effects counted from each note (no new attack; a's fall doesn't play), as a slide made by hand
    does, like a synth's legato. Paired as Glide pairs them (one to a chord, several to one, chord to chord none);
    the same pitch too (an arpeggio of one note held: one envelope)."""
    if not (voice or {}).get("legato") or len(tones) < 2:
        return []
    ended = sorted(tones, key=lambda n: n["t"] + n["len"])
    ends = [n["t"] + n["len"] for n in ended]
    out, i = [], 0
    order = sorted(tones, key=lambda n: n["t"])
    while i < len(order):
        t = order[i]["t"]
        j = i
        while j < len(order) and order[j]["t"] <= t + 1e-9:
            j += 1
        chord, i = order[i:j], j
        before = ended[bisect.bisect_left(ends, t - 1e-9):bisect.bisect_right(ends, t + 1e-9)]
        if before and not (len(before) > 1 and len(chord) > 1):
            out += [(a, b) for b in chord for a in before if a is not b]
    return out


def glide_left(u, curve):
    """How much of a glide's way is left at u (0..1 of its time): (1 - u) ^ 4^curve, so curve 0 = straight, 1 = fast
    first, -1 = slow first (GLIDE_CURVE = (1 - u)^2, as glides always were)."""
    return (1.0 - u) ** (4.0 ** curve)
