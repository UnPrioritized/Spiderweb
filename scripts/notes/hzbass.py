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
"key": its tone, "in": lead in, "out": lead out (beats)}, ...]. Tones sounding together are a chord: each gets its
own line (voices), a line being tones one after the other. Along a line the tone jumps at the next tone's start;
with a lead out / in it slides instead, from `out` before the end of one tone to `in` after the start of the next
(the red line in the window). Each line makes repeats one wave apart (the wave getting shorter or longer along a
slide); the lines' repeats together, each lasting until the next one starts, are the squares every key of the
shape is chopped by (custom.chop_grid), so a chord takes one channel. Nothing sounds where no tone is.
hz["grow"] = the shape is kept as long as its tones (fit_length). hz["own"] = made with the Hz bass tool: a box
that is nothing but its tones (it goes when its last tone is deleted; the panel shows the keys it repeats)."""

import functools
import json
import math

import numpy as np

HZ_DEFAULTS = {"key": 33, "cents": 0.0}
MIN_LEN = 1 / 1024  # beats: a tone is never shorter


def hz_of(key, cents=0.0):
    """The tone of a key in Hz (key 69 = 440 Hz)."""
    return 440.0 * 2.0 ** ((key - 69 + cents / 100.0) / 12.0)


def hz_gate(hz, bpm):
    """The spam gate, in beats, that sounds like hz's tone at this BPM."""
    return max(1e-6, float(f"{bpm / (60.0 * hz_of(hz['key'], hz['cents'])):.12g}"))  # (12 digits: as saved)


def clean_tones(tones):
    """Placed tones checked and put in order (by start, then key)."""
    out = []
    for n in tones if isinstance(tones, list) else ():
        try:
            tone = {"t": max(0.0, float(n["t"])), "len": max(MIN_LEN, float(n["len"])), "key": int(n["key"]),
                    "in": max(0.0, float(n.get("in", 0.0))), "out": max(0.0, float(n.get("out", 0.0)))}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if 0 <= tone["key"] <= 127 and all(math.isfinite(v) for v in tone.values()):
            out.append(tone)
    return sorted(out, key=lambda n: (n["t"], n["key"]))


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


def voices(tones):
    """The tones as lines: each a list of tones one after the other (a tone goes on the first line that's free)."""
    lines = []
    for n in sorted(tones, key=lambda n: (n["t"], n["key"])):
        for line in lines:
            if line[-1]["t"] + line[-1]["len"] <= n["t"] + 1e-9:
                line.append(n)
                break
        else:
            lines.append([n])
    return lines


def holds(line):
    """[(a, b)] for each tone of a line: it holds its own tone from a to b (beats); before a it's still sliding in
    from the tone before, after b it's sliding out to the next one."""
    out = []
    for i, n in enumerate(line):
        a = n["t"] + (min(n["in"], n["len"]) if i else 0.0)
        b = n["t"] + n["len"] - (min(n["out"], n["t"] + n["len"] - a) if i + 1 < len(line) else 0.0)
        out.append((a, b))
    return out


def pieces(line):
    """A line as [(start, end, key at the start, key at the end)] in beats: only where it sounds."""
    hs = holds(line)

    def slide(i, t):  # the tone at time t of the slide from tone i to the next
        x0, x1 = hs[i][1], hs[i + 1][0]
        k0, k1 = line[i]["key"], line[i + 1]["key"]
        return k0 if x1 - x0 < 1e-12 else k0 + (k1 - k0) * (t - x0) / (x1 - x0)

    out = []
    for i, n in enumerate(line):
        (a, b), start, end = hs[i], n["t"], n["t"] + n["len"]
        if a > start:
            out.append((start, a, slide(i - 1, start), float(n["key"])))
        if b > a:
            out.append((a, b, float(n["key"]), float(n["key"])))
        if end > b:
            out.append((b, end, float(n["key"]), slide(i, end)))
    return out


def wave(hz, ppq, key):
    """The gate, in ticks, of one wave of key's tone (hz = the shape's settings: cents, bpm, fixed)."""
    gate = ppq * hz["bpm"] / 60.0 / hz_of(key, hz["cents"])
    return max(1.0, math.floor(gate + 0.5) if hz.get("fixed") else gate)


def line_repeats(line, left, ppq, hz):
    """One line's repeats as arrays (start tick, the tick its run of sound ends at)."""
    ps = pieces(line)
    starts, limits = [], []
    t, run_end, limit = 0.0, None, 0
    for j, (p0, p1, k0, k1) in enumerate(ps):
        s, e = (left + p0) * ppq, (left + p1) * ppq
        if run_end is None or abs(p0 - run_end) > 1e-9:  # after a silence: the waves start again here
            t = s
            k = j
            while k + 1 < len(ps) and abs(ps[k + 1][0] - ps[k][1]) <= 1e-9:
                k += 1
            limit = math.floor((left + ps[k][1]) * ppq + 0.5)
        run_end = p1
        if t >= e:
            continue
        if k0 == k1:
            gate = wave(hz, ppq, k0)
            n = int(math.ceil((e - t) / gate))
            starts.append(t + gate * np.arange(n))
            t += n * gate
        else:
            part = []
            while t < e:
                part.append(t)
                t += wave(hz, ppq, k0 + (k1 - k0) * (t - s) / (e - s))
            starts.append(np.array(part))
        limits.append(np.full(len(starts[-1]), limit, np.int64))
    if not starts:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.floor(np.concatenate(starts) + 0.5).astype(np.int64), np.concatenate(limits)


@functools.lru_cache(maxsize=16)
def _squares(hz_json, left, ppq):
    hz = json.loads(hz_json)
    got = [line_repeats(line, left, ppq, hz) for line in voices(hz["tones"])]
    starts = np.concatenate([s for s, _ in got]) if got else np.zeros(0, np.int64)
    limits = np.concatenate([l for _, l in got]) if got else np.zeros(0, np.int64)
    if not len(starts):
        return np.zeros((0, 2), np.int64)
    order = np.argsort(starts, kind="stable")
    starts, limits = starts[order], limits[order]
    first = np.concatenate([[True], starts[1:] != starts[:-1]])
    at = np.flatnonzero(first)
    starts, limits = starts[at], np.maximum.reduceat(limits, at)  # (repeats of two lines on one tick: one note)
    ends = np.minimum(np.concatenate([starts[1:], limits[-1:]]), limits)
    out = np.column_stack([starts, ends])[ends > starts]
    out.setflags(write=False)
    return out


def squares(sh, ppq):
    """The repeats of a shape with placed tones: an array of (start, end) ticks in order, none overlapping."""
    return _squares(json.dumps(sh["hz"], sort_keys=True), left_edge(sh), ppq)


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
    return wave(hz, ppq, max((n["key"] for n in hz.get("tones") or ()), default=hz["key"]))
