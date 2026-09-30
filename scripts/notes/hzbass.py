"""Hz bass: spam so fast that the repeats sound like a tone.

A custom shape with sh["hz"] (and a spam fill) gets its spam gates from a tone instead of the gate box:
  {"key": the key whose tone is wanted, "cents": pitch adjustment (100 = one key), "bpm": the BPM the gates are
   worked out for, "fixed": True = every gate rounded to a whole tick instead (the tone is a little off; the
   higher PPQ x BPM, the less)}
sh["gate"] is then one wave of that tone, gate = BPM / (60 x Hz) beats, NOT rounded to a whole tick: the notes sit
on one grid counted from tick 0 (every key in step), note n starting at round(n x gate), so gates of two sizes are
mixed and the tone comes out exact (custom.chop_even). The tone depends on the BPM, so a changed BPM leaves it off
until it's updated (the panel warns); a changed PPQ keeps the tone.

Placed notes (the Hz bass window): hz["tones"] = [{"t": start in beats from the shape's left edge, "len": beats,
"key": its tone, "cents": its own tune, "id": its number (never reused in the shape), "to": its slides}, ...].
Each tone makes repeats one wave apart for as long as it lasts; tones sounding together are a chord. A slide is
made by the user and belongs to two tones: "to" = [{"id": the tone slid to, "out": lead out, "in": lead in
(beats)}, ...]: the tone glides from `out` before this tone's end to `in` after the start of the other one (which
must start at or after this one's end), the wave getting shorter or longer on the way. A tone can slide to several
tones, and several can slide to one: each slide is a line of its own, next to the tone held (tone_runs). Without a
slide the tone just stops at its end. The red line in the window is what is really heard (heard).
All the repeats together, each lasting until the next one starts, are the squares every key of the shape is chopped
by (custom.chop_grid), so a chord takes one channel. Nothing sounds where no tone is.
hz["grow"] = the shape is kept as long as its tones (fit_length). hz["own"] = made with the Hz bass tool: a box
that is nothing but its tones (it goes when its last tone is deleted; the panel shows the keys it repeats).

Effects: a tone can have "fx" = {effect: [[u, value], ...]}: a line through points, u = 0..1 along the tone, value
0..1. They change the colour of the tone, not its pitch, by making the keys hit at different spots of the wave
(how late a key is, in waves, is added up over the effects; only the part after the comma counts):
  "slant": every key starts its repeats a bit later than the key below it; the value is how much of one wave the
  shape's keys are spread over (0 = all together).
  "groups": the keys take turns in groups, evenly spread over the wave: 0 = all together, then 2, 3... up to
  GROUPS groups at 1 (group_count).
  "offpitch": every key repeats a little faster or slower than the tone, the lowest key the fastest, the highest
  the slowest: the keys drift apart and meet again by themselves. 1 = OFF_PITCH of the tone between them.
  "noisy": every repeat of every key is late by a random bit, up to the value of one wave.
With effects every key has its own repeats (KeyGrid, custom.chop_keys); without any, nothing changes.
Four more change how hard the keys hit instead (velocity_factor, used by engine._notes_tracks on top of the shape's
own velocity; loudness goes with velocity squared):
  "sweep": a bump of loudness over the keys; the value is where it is, 0 = the lowest key, 1 = the highest.
  "wah": loud and quiet stripes over the keys, more of them the higher the value (0 = every key full).
  "tremolo": every key louder and quieter in turn, value x TREMOLO times a beat (0 = steady).
  "octave": every other repeat softer, down to velocity 1 at 1: the tone an octave below comes in."""

import functools
import json
import math

import numpy as np

HZ_DEFAULTS = {"key": 33, "cents": 0.0}
MIN_LEN = 1 / 1024  # beats: a tone is never shorter
TUNE = 50.0  # cents: how far a placed tone's own tune goes, up or down (half a key)
VEL_FX = ("sweep", "wah", "tremolo", "octave")  # the effects that change the velocity
FX = ("slant", "groups", "offpitch", "noisy") + VEL_FX  # the effects a placed tone can have
FX_START = {"slant": [[0.0, 0.0], [1.0, 1.0]], "groups": [[0.0, 0.0], [1.0, 1.0]],  # the line an effect starts with
            "offpitch": [[0.0, 0.5], [1.0, 0.5]], "noisy": [[0.0, 0.0], [1.0, 1.0]],
            "sweep": [[0.0, 0.0], [1.0, 1.0]], "wah": [[0.0, 0.0], [1.0, 1.0]],
            "tremolo": [[0.0, 0.5], [1.0, 0.5]], "octave": [[0.0, 0.0], [1.0, 1.0]]}
WAH = 8.0  # "wah" at 1: this many loud stripes over the keys
TREMOLO = 8.0  # "tremolo" at 1: this many times a beat
GROUPS = 6  # "groups" at 1
OFF_PITCH = 0.02  # "offpitch" at 1: the highest key's tone is this much (x the tone) below the lowest key's


def group_count(value):
    """How many groups the keys take turns in at a "groups" value (an array or a number)."""
    return 1 + np.floor(np.asarray(value) * (GROUPS - 1) + 0.5).astype(np.int64)


def hz_of(key, cents=0.0):
    """The tone of a key in Hz (key 69 = 440 Hz)."""
    return 440.0 * 2.0 ** ((key - 69 + cents / 100.0) / 12.0)


def hz_gate(hz, bpm):
    """The spam gate, in beats, that sounds like hz's tone at this BPM."""
    return max(1e-6, float(f"{bpm / (60.0 * hz_of(hz['key'], hz['cents'])):.12g}"))  # (12 digits: as saved)


def clean_fx(fx):
    """A tone's effects checked: {effect: [[u, value], ...]} in order, both 0..1. Effects with no points are left
    out."""
    out = {}
    for name in FX:
        try:
            pts = [(float(u), float(v)) for u, v in fx.get(name) or ()]
        except (TypeError, ValueError, AttributeError):
            continue
        pts = sorted(((min(1.0, max(0.0, u)), min(1.0, max(0.0, v))) for u, v in pts
                      if math.isfinite(u) and math.isfinite(v)), key=lambda p: p[0])  # (two at one spot: a step)
        if pts:
            out[name] = [list(p) for p in pts]
    return out


def fx_at(n, name, beat):
    """The value of a tone's effect at beat (an array, from the shape's left edge; 0 when it hasn't got it).
    Before the first point and after the last one the line stays flat."""
    pts = (n.get("fx") or {}).get(name)
    if not pts:
        return np.zeros(np.shape(beat))
    return np.interp((np.asarray(beat) - n["t"]) / n["len"], [p[0] for p in pts], [p[1] for p in pts])


def has_fx(hz):
    return any(n.get("fx") for n in hz.get("tones") or ())


def clean_tones(tones):
    """Placed tones checked and put in order (by start, then key)."""
    out, old = [], []
    for n in tones if isinstance(tones, list) else ():
        try:
            tone = {"t": max(0.0, float(n["t"])), "len": max(MIN_LEN, float(n["len"])), "key": int(n["key"]),
                    "cents": max(-TUNE, min(TUNE, float(n.get("cents", 0.0))))}
            leads = (max(0.0, float(n.get("in", 0.0))), max(0.0, float(n.get("out", 0.0))))
            tone["id"] = int(n.get("id", 0))
            tone["to"] = [{"id": int(s["id"]), "out": max(0.0, float(s["out"])), "in": max(0.0, float(s["in"]))}
                          for s in n.get("to") or ()]
            fx = clean_fx(n.get("fx"))
            if fx:
                tone["fx"] = fx
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        ok = all(math.isfinite(v) for v in [tone["t"], tone["len"], tone["cents"], *leads]
                 + [v for s in tone["to"] for v in (s["out"], s["in"])])
        if 0 <= tone["key"] <= 127 and ok:
            out.append(tone)
            old.append(leads if "id" not in n else None)
    order = sorted(range(len(out)), key=lambda i: (out[i]["t"], out[i]["key"]))
    out, old = [out[i] for i in order], [old[i] for i in order]
    seen = set()
    for n in out:  # every tone its own number
        if n["id"] <= 0 or n["id"] in seen:
            n["id"] = max([m["id"] for m in out] + [0]) + 1
        seen.add(n["id"])
    # saved before slides were their own thing (a lead in / out on each tone): it slid to the next tone on its line
    lines = []
    for n, leads in zip(out, old):
        if leads is None:
            continue
        for line in lines:
            a, a_leads = line[-1]
            if a["t"] + a["len"] <= n["t"] + 1e-9:
                if a_leads[1] > 0 or leads[0] > 0:
                    a["to"].append({"id": n["id"], "out": a_leads[1], "in": leads[0]})
                line.append((n, leads))
                break
        else:
            lines.append([(n, leads)])
    for n in out:  # no slide to a tone that's gone, none twice
        kept = {}
        for s in n["to"]:
            if s["id"] in seen and s["id"] != n["id"]:
                kept.setdefault(s["id"], s)
        n["to"] = list(kept.values())
    return out


def clean_hz(hz):
    """A saved "hz" checked, or None."""
    try:
        out = {"key": int(hz["key"]), "cents": float(hz.get("cents", 0.0)), "bpm": float(hz["bpm"])}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not (0 <= out["key"] <= 255 and abs(out["cents"]) <= 1200 and out["bpm"] > 0):
        return None
    if hz.get("fixed") is True:
        out["fixed"] = True
    tones = clean_tones(hz.get("tones"))
    if tones:
        out["tones"] = tones
        for flag in ("grow", "own"):
            if hz.get(flag) is True:
                out[flag] = True
    return out


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


def wave(hz, ppq, key):
    """The gate, in ticks, of one wave of key's tone (hz = the shape's settings: cents, bpm, fixed)."""
    gate = ppq * hz["bpm"] / 60.0 / hz_of(key, hz["cents"])
    return max(1.0, math.floor(gate + 0.5) if hz.get("fixed") else gate)


def tone_runs(hz, left, ppq):
    """The repeats of the placed tones as unbroken stretches of tone: [(start ticks, the ticks their waves are over
    = the next one's start, whose: (tone, None) or (tone slid from, tone slid to))], not rounded. A tone held is
    one stretch, from where the first slide into it arrives to where the last slide out of it leaves; every slide
    is one more (two when there's a gap between its tones: nothing sounds there), its waves in step with the tone
    it leaves."""
    tones = hz["tones"]
    ls = links(tones)
    out, held = [], {}
    for n in tones:
        a = n["t"] + min([min(s["in"], n["len"]) for _, b, s in ls if b is n], default=0.0)
        b = n["t"] + n["len"] - min([min(s["out"], n["len"]) for m, _, s in ls if m is n], default=0.0)
        if b > a:
            s, e, gate = (left + a) * ppq, (left + b) * ppq, wave(hz, ppq, pitch(n))
            starts = s + gate * np.arange(int(math.ceil((e - s) / gate)))
            out.append((starts, starts + gate, (n, None)))
            held[n["id"]] = (s, gate)
    for a, b, link in ls:
        x0, x1, k0, k1 = glide(a, b, link)
        if x1 - x0 < 1e-12:
            continue
        s0, e0 = (left + x0) * ppq, (left + a["t"] + a["len"]) * ppq
        s1, e1 = (left + b["t"]) * ppq, (left + x1) * ppq
        t = s0
        if a["id"] in held and s0 >= held[a["id"]][0]:  # in step with the tone it leaves
            s, gate = held[a["id"]]
            t = s + math.ceil((s0 - s) / gate - 1e-9) * gate
        gap = s1 - e0 > 1e-6
        for end in (e0, e1) if gap else (e1,):
            part, after = [], []
            while t < end:
                part.append(t)
                t += wave(hz, ppq, k0 + (k1 - k0) * (t - s0) / (e1 - s0))
                after.append(t)
            if part:
                out.append((np.array(part), np.array(after), (a, b)))
            t = max(t, s1)
    return out


def _limits(tones, left, ppq, starts):
    """For each repeat (start ticks, not rounded): the tick the sound it's in ends at (the end of the tones that
    touch or overlap around it)."""
    spans = []
    for n in sorted(tones, key=lambda n: n["t"]):
        if spans and n["t"] <= spans[-1][1] + 1e-9:
            spans[-1][1] = max(spans[-1][1], n["t"] + n["len"])
        else:
            spans.append([n["t"], n["t"] + n["len"]])
    at = np.array([(left + a) * ppq for a, _ in spans]) - 1e-6
    ends = np.array([math.floor((left + b) * ppq + 0.5) for _, b in spans], np.int64)
    return ends[np.maximum(np.searchsorted(at, starts, "right") - 1, 0)]


def _whole(v):
    return np.floor(v + 0.5).astype(np.int64)


@functools.lru_cache(maxsize=16)
def _heard(hz_json, left, ppq, bpm):
    hz = json.loads(hz_json)
    out = []
    for starts, nexts, _ in tone_runs(hz, left, ppq):
        limits = _limits(hz["tones"], left, ppq, starts)
        mean = nexts - starts  # (the wave as made: with mixed gates the whole-tick ones come to this on average)
        starts, nexts = _whole(starts), _whole(nexts)
        gates = np.maximum(nexts - starts, 1)  # (whole ticks: what the PPQ lets the wave be)
        keys, mean = (69.0 + 12.0 * np.log2(ppq * bpm / 60.0 / 440.0 / g) - hz["cents"] / 100.0
                      for g in (gates, mean))
        ends = np.minimum(nexts, limits)
        keep = ends > starts
        if keep.any():
            out.append((starts[keep] / ppq - left, ends[keep] / ppq - left, keys[keep], mean[keep]))
    return out


def heard(hz, left, ppq, bpm):
    """What the placed tones really sound like at this PPQ and BPM (the red line of the Hz bass window): for each
    unbroken stretch of tone, arrays (start, end in beats from the left edge, pitch in keys, average pitch) of its
    repeats. A repeat lasts a whole number of ticks, so its pitch is a little off the wanted one: the lower the
    PPQ, the more. Mixed gates take turns so that the average is the wanted tone; fixed gates are all the same, so
    there the average is each repeat's own pitch.
    The pitch is counted from the shape's own tuning (hz["cents"]), so a key's exact tone is that key."""
    return _heard(json.dumps(hz, sort_keys=True), left, ppq, float(bpm))


@functools.lru_cache(maxsize=16)
def _squares(hz_json, left, ppq):
    hz = json.loads(hz_json)
    got = [s for s, _, _ in tone_runs(hz, left, ppq)]
    if not got:
        return np.zeros((0, 2), np.int64)
    starts = np.concatenate(got)
    return _grid(starts, _limits(hz["tones"], left, ppq, starts))


def _grid(starts, limits):
    """Repeats (start ticks, not rounded; the tick each one's sound ends at) -> (start, end) whole ticks in order,
    each lasting until the next one starts."""
    starts = _whole(starts)
    order = np.argsort(starts, kind="stable")
    starts, limits = starts[order], limits[order]
    first = np.concatenate([[True], starts[1:] != starts[:-1]])
    at = np.flatnonzero(first)
    starts, limits = starts[at], np.maximum.reduceat(limits, at)  # (repeats of two lines on one tick: one note)
    ends = np.minimum(np.concatenate([starts[1:], limits[-1:]]), limits)
    out = np.column_stack([starts, ends])[ends > starts]
    out.setflags(write=False)
    return out


class KeyGrid:
    """The repeats of a Hz bass whose tones have effects: every key has its own (squares(key))."""

    def __init__(self, hz, left, ppq, lo, n):
        self.lo, self.n, self.got = lo, max(1, n), {}
        runs = tone_runs(hz, left, ppq)
        self.starts = np.concatenate([s for s, _, _ in runs]) if runs else np.zeros(0)
        if runs:
            self.waves = np.concatenate([e - s for s, e, _ in runs])
            fx = {name: [self.value(name, whose, s / ppq - left) for s, _, whose in runs] for name in FX}
            self.slant, self.noisy = np.concatenate(fx["slant"]), np.concatenate(fx["noisy"])
            self.groups = group_count(np.concatenate(fx["groups"]))
            # off pitch: every repeat moves the key on by a bit of a wave, so it adds up along the stretch of tone
            self.drift = np.concatenate([np.cumsum(v) * OFF_PITCH for v in fx["offpitch"]])
            self.limits = _limits(hz["tones"], left, ppq, self.starts)
            # a repeat moved past its own tone's end is left out (the next tone may touch it: not its sound)
            self.until = np.concatenate([np.full(len(s), (left + a["t"] + a["len"]) * ppq if b is None else np.inf)
                                         for s, _, (a, b) in runs])

    @staticmethod
    def value(name, whose, beat):
        """An effect's value at each repeat of a stretch of tone: its tone's line; on a slide the line of the tone
        it leaves, then (from that one's start) the line of the tone it goes to."""
        a, b = whose
        if b is None:
            return fx_at(a, name, beat)
        return np.where(beat < b["t"], fx_at(a, name, beat), fx_at(b, name, beat))

    def squares(self, key):
        """A key's repeats: (start, end) ticks in order, none overlapping."""
        if not len(self.starts):
            return np.zeros((0, 2), np.int64)
        x = min(1.0, max(0.0, (key - self.lo) / self.n))  # where the key is among the shape's keys, 0 = the lowest
        sq = self.got.get(x)
        if sq is None:
            late = self.slant * x + np.floor(x * self.groups) / self.groups + self.drift * (x - 0.5)
            if self.noisy.any():  # (the same every time: the key is the seed)
                late = late + self.noisy * np.random.default_rng(1000 + key).random(len(late))
            starts = self.starts + np.mod(late, 1.0) * self.waves
            keep = starts < self.until - 1e-6
            sq = self.got[x] = _grid(starts[keep], self.limits[keep])
        return sq


@functools.lru_cache(maxsize=16)
def _key_grid(hz_json, left, ppq, lo, n):
    return KeyGrid(json.loads(hz_json), left, ppq, lo, n)


def key_range(sh):
    """The lowest and highest key of a custom shape's box."""
    ps = [p for _, p in sh["pts"]]
    ps.append(ps[1] + ps[2] - ps[0])
    return round(min(ps)), round(max(ps))


def velocity_factor(sh, ppq, starts, keys):
    """What the velocity effects make of the velocity of a shape's notes (arrays: start ticks, keys): a number
    from 0 to 1 for each to multiply it by, or None when no tone has such an effect. A note gets the effects of
    the first tone that sounds where it starts."""
    hz = sh["hz"]
    tones = [n for n in hz["tones"] if any(name in (n.get("fx") or ()) for name in VEL_FX)]
    if not tones or not len(starts):
        return None
    left = left_edge(sh)
    lo, hi = key_range(sh)
    x = np.clip((keys - lo) / max(1, hi - lo), 0.0, 1.0)  # 0 = the lowest key, 1 = the highest
    beat = starts / ppq - left
    out = np.ones(len(starts))
    free = np.ones(len(starts), bool)
    for n in tones:
        at = np.flatnonzero(free & (beat >= n["t"] - 1e-9) & (beat < n["t"] + n["len"]))
        if not len(at):
            continue
        free[at] = False
        fx, b, xk = n["fx"], beat[at], x[at]
        loud = np.ones(len(at))
        if "sweep" in fx:
            loud *= 0.08 + 0.92 * np.clip(np.cos(np.pi * (xk - fx_at(n, "sweep", b))), 0.0, 1.0) ** 4
        if "wah" in fx:
            loud *= (1.0 + np.cos(2.0 * np.pi * WAH * fx_at(n, "wah", b) * (xk - 0.5))) / 2.0
        if "tremolo" in fx:  # how often it has gone up and down so far: its speed added up along the tone
            us = np.linspace(0.0, 1.0, 513)
            speed = fx_at(n, "tremolo", n["t"] + us * n["len"]) * TREMOLO * n["len"]
            turns = np.concatenate([[0.0], np.cumsum((speed[1:] + speed[:-1]) / 2.0) / 512.0])
            loud *= 0.1 + 0.9 * (1.0 + np.cos(2.0 * np.pi * np.interp((b - n["t"]) / n["len"], us, turns))) / 2.0
        factor = np.sqrt(loud)
        if "octave" in fx:
            # (which repeat of the tone: the starts are whole ticks, up to half a tick before their real spot)
            number = np.floor((starts[at] + 0.5 - (left + n["t"]) * ppq) / wave(hz, ppq, pitch(n)) + 1e-6)
            factor = np.where(number % 2 == 1, factor * (1.0 - fx_at(n, "octave", b)), factor)
        out[at] = factor
    return out


def squares(sh, ppq):
    """The repeats of a shape with placed tones: an array of (start, end) ticks in order, none overlapping. When
    its tones have effects: a KeyGrid (each key has its own)."""
    hz_json = json.dumps(sh["hz"], sort_keys=True)
    if has_fx(sh["hz"]):
        lo, hi = key_range(sh)
        return _key_grid(hz_json, left_edge(sh), ppq, lo, hi - lo + 1)
    return _squares(hz_json, left_edge(sh), ppq)


def left_edge(sh):
    """The beat a custom shape's box starts at (its tones count from there)."""
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    return min(b0, b1, b2, b1 + b2 - b0)


def fit_length(sh):
    """hz["grow"]: the shape made as long as its tones (stretched from its left edge)."""
    span = max(tones_span(sh["hz"]["tones"]), MIN_LEN)
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    left, width = min(bs), max(bs) - min(bs)
    if width > 1e-12:
        for p in sh["pts"]:
            p[0] = left + (p[0] - left) * span / width


def shortest_gate(hz, ppq):
    """The shortest gate, in ticks, a shape's Hz bass uses (its highest tone)."""
    return wave(hz, ppq, max((pitch(n) for n in hz.get("tones") or ()), default=hz["key"]))
