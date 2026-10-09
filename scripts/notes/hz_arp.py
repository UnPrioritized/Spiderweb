"""Hz bass arpeggio: the runs it makes from the notes held (patterns, custom steps, scales, chords), with its Speed /
Gate / Swing moved by the MOD tab."""

import math

import numpy as np

from notes.hz_settings import ARP_KNOBS, CHORDS, MIN_LEN, SCALES, TIME_STEP
from notes.hz_modulate import setting_at


def in_scale(key, scale, root):
    """A key moved to the nearest key of the scale (SCALES) counted from root (0 = C); half way: down; one that
    would leave the keys (0..127) goes the other way."""
    steps = SCALES[scale]
    for d in (0, -1, 1, -2, 2, -3, 3, -4, 4):
        if (key + d - root) % 12 in steps and 0 <= key + d <= 127:
            return key + d
    return key


def arpeggiated(tones, arp, left=0.0, hz=None):
    """The tones as the Arpeggio box plays them, as a synth's arpeggiator: from the first note's start a run of short
    tones, one every 1 / speed beats while any note is held, through the held notes' pitches (with the chord's steps
    and octaves) in the pattern's order; a note pressed meanwhile joins the run at its next step, a note let go drops
    out (user 2026-10-09). Once nothing is held the run ends; the next note starts a new one. Each keeps its note's tune and gates;
    slides made by hand are left out (the run's notes are new ones). The "steps" pattern: the box's own steps
    (STEPS) pick the held notes, low to high, each with its octave, loudness ("level" on the tone) and length.
    hz = the Hz bass played (its tones = these), for the MOD tab moving Speed / Gate / Swing (arp_moved)."""
    moved = {link["to"] for link in ((hz or {}).get("mod") or {}).get("links", ()) if link["to"] in ARP_KNOBS}
    if moved:  # (a private copy: what the sources need (the chains...) worked out once, not for every run)
        hz = dict(hz, _memo={})
    step = 1.0 / arp["speed"]
    late = arp.get("swing", 0.0) * step / 2  # (every second step: that much later, the one before it longer)
    steps = arp["steps"][:arp["count"]] if arp["pattern"] == "steps" else None

    def at(k, t0):
        """Step k from t0: (its start, how long its slot is)."""
        t = t0 + k * step
        # (swing by the beat grid, counted from the Hz bass's start as a synth counts from the song's: the steps
        # between the grid's beats come late, wherever the chord starts; user, 2026-10-08)
        odd = bool(late) and math.floor(t / step + 0.5) % 2 == 1
        return (t + late, step - late) if odd else (t, step + late)
    out = []
    order = sorted(tones, key=lambda n: n["t"])
    i = 0
    while i < len(order):
        t0, end = order[i]["t"], order[i]["t"] + order[i]["len"]
        j = i + 1
        # (pressed while one is held; one starting right where the run ends starts a new run, as a synth gets the
        # let-go first: notes end to end, hunt 2026-10-09)
        while j < len(order) and order[j]["t"] < end - 1e-9:
            end = max(end, order[j]["t"] + order[j]["len"])
            j += 1
        chord, i = order[i:j], j
        items = []  # ((key, tune), the note it's from), low to high; one pitch from two notes: the longer first
        for n in sorted(chord, key=lambda n: -n["len"]):
            for k in CHORDS[arp["chord"]]:
                for o in range(1 if steps else int(arp["octaves"])):  # (steps: their own octaves, below)
                    key = n["key"] + k + 12 * o
                    if arp.get("scale") and not steps:  # (moved into the scale: two landing on one key = one)
                        key = in_scale(key, arp["scale"], arp.get("root", 0))
                    items.append(((key, n["cents"]), n))
        items.sort(key=lambda kv: kv[0][0] + kv[0][1] / 100)  # (stable: the longer note first on one pitch)
        # (Random: drawn from the chord's beat in the song (left = the shape's edge), so a Split or a moved edge
        # leaves it as it was)
        rng = np.random.default_rng(int(round((left + t0) * 1000)) % (2 ** 32))
        if moved:
            when, gate = arp_moved(hz, arp, moved, chord, t0, end)
        else:
            when, gate = (lambda j, t0=t0: at(j, t0)), (lambda j: arp["gate"])
        k = 0
        while True:
            t, slot = when(k)
            if t >= end - 1e-9:
                break
            held, seen = [], set()  # (the pitches held at this step, each once)
            for key, n in items:
                if n["t"] <= t + 1e-9 and n["t"] + n["len"] > t + 1e-9 and key not in seen:
                    seen.add(key)
                    held.append((key, n))
            if steps:
                tone = stepped(steps, k, t, held, arp, when, end, gate)
                if tone:
                    tone["id"] = len(out) + 1
                    out.append(tone)
            elif held:
                seq = held if arp["pattern"] != "down" else held[::-1]
                if arp["pattern"] == "updown" and len(held) > 2:
                    seq = held + held[-2:0:-1]
                pick = seq[int(rng.integers(len(seq)))] if arp["pattern"] == "random" else seq[k % len(seq)]
                (key, cents), n = pick
                if 0 <= key <= 127:
                    tone = {"t": t, "len": max(MIN_LEN, min(slot * gate(k), n["t"] + n["len"] - t)), "key": key,
                            "cents": cents, "id": len(out) + 1, "to": []}
                    tone.update({f: n[f] for f in ("auto", "gate") if f in n})
                    out.append(tone)
            k += 1
    return sorted(out, key=lambda n: (n["t"], n["key"]))


def stepped(steps, k, t, held, arp, at, end, gate):
    """The "steps" pattern's tone at step k (at t; held = the pitches still held, low to high; at(j) = step j's
    (start, slot); end = when the last note is let go; gate(j) = step j's Gate), or None: a rest, nothing held, one
    held on by the step before (tie)."""
    s, count = steps[k % len(steps)], len(steps)
    if k and steps[(k - 1) % count].get("tie") or not s["note"] or s["level"] <= 0 or not held:
        return None
    (key, cents), n = held[(s["note"] - 1) % len(held)]
    key += 12 * (s["octave"] + (k // count) % int(arp["octaves"]))  # (Octaves: each time round, one higher)
    if arp.get("scale"):
        key = in_scale(key, arp["scale"], arp.get("root", 0))
    if not 0 <= key <= 127:
        return None
    j = k  # (tied: on to the end of the last step it's tied through)
    while steps[j % count].get("tie") and at(j + 1)[0] < end - 1e-9:
        j += 1
    tj, slot = at(j)
    tone = {"t": t, "len": max(MIN_LEN, min(tj - t + slot * steps[j % count]["length"] * gate(j),
                                            n["t"] + n["len"] - t)),
            "key": key, "cents": cents, "to": []}
    if s["level"] < 1.0:
        tone["level"] = s["level"]
    tone.update({f: n[f] for f in ("auto", "gate") if f in n})
    return tone


def arp_moved(hz, arp, moved, chord, t0, end):
    """The Arpeggio's run from t0 (a chord let go at end) while the MOD tab moves its Speed / Gate / Swing (moved):
    (at(k) = step k's (start, slot), gate(k) = its Gate), the sources read for the run's note held longest. Speed:
    the steps added up finely, so the run goes faster or slower from where it is; Swing and Gate: as each step
    starts (the steps between the grid's beats counted as if the run went at its first Speed from the start)."""
    tone = max(chord, key=lambda n: n["len"])
    span = max(end - t0, MIN_LEN)
    dt = max(TIME_STEP, span / 100000)
    g = t0 + np.arange(int(span / dt) + 2) * dt
    speed = (setting_at(hz, "arp_speed", arp["speed"], g, tone, {}) if "arp_speed" in moved
             else np.full(len(g), arp["speed"]))
    done = np.concatenate([[0.0], np.cumsum(speed[:-1] * dt)])  # (steps gone by)
    k = np.arange(int(done[-1]) + 5, dtype=float)
    t = np.interp(k, done, g)
    past = k > done[-1]  # (past the grid: on at the last Speed)
    t[past] = g[-1] + (k[past] - done[-1]) / speed[-1]
    swing = (setting_at(hz, "arp_swing", arp.get("swing", 0.0), t, tone, {}) if "arp_swing" in moved
             else np.full(len(t), arp.get("swing", 0.0)))
    odd = np.floor(t0 * speed[0] + k + 0.5) % 2 == 1
    start = t[:-1] + np.where(odd[:-1], swing[:-1] * np.diff(t) / 2, 0.0)  # (late by up to half its step)
    slot = np.diff(start)
    start = start[:-1]
    gates = (setting_at(hz, "arp_gate", arp["gate"], start, tone, {}) if "arp_gate" in moved
             else np.full(len(start), arp["gate"]))

    def at(j):
        return (float(start[j]), float(slot[j])) if j < len(start) else (math.inf, 0.0)
    return at, (lambda j: float(gates[min(j, len(gates) - 1)]))
