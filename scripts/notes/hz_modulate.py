"""Hz bass MOD tab engine: effect lines and knobs moved over time by the LFOs, envelopes, velocity and pitch, time
knobs speeding up or slowing down a running envelope."""

import math

import numpy as np

from notes.hz_settings import (ADSR_KNOBS, ARP_KNOBS, BLEND, FAST, GLIDE_CURVE, HZ_DEFAULTS, LFO_RATES, LOOP,
                               MOD_ENVS, MOD_LFOS, MOD_RACK, MOD_SETTINGS, NEUTRAL, OSC2, OSC2_TUNE, TIMED_KNOBS,
                               TIMED_NAMES, TIME_STEP, TREMOLO_DEPTH, VIBRATO_RATE, WAVES, mod_start, osc2_shift,
                               rack_on)
from notes.hz_lines import adsr_line, env_value, line_at, loop_shape, same_points, sustained
from notes.hz_glide import cached, note_beats, note_span, pitch


def rack_tail(hz):
    """Beats the Effects tab's echo and reverb make the sound go on for after the notes."""
    echo, reverb = rack_on(hz, "echo"), rack_on(hz, "reverb")
    linked = {link["to"] for link in (hz.get("mod") or {}).get("links", ())}  # (moved: as long as they can get)
    time = setting_most(hz, "echo_time", echo["time"]) if echo and "echo_time" in linked else echo and echo["time"]
    length = (setting_most(hz, "reverb_length", reverb["length"]) if reverb and "reverb_length" in linked
              else reverb and reverb["length"])
    return (echo["repeats"] * time if echo else 0.0) + (length if reverb else 0.0)


def fx_at(hz, name, beat, tone=None, sources=None):
    """The value of an effect at beat (an array, from the shape's left edge; 0 when there's no such line).
    Before the first point and after the last one the line stays flat, unless it repeats (hz["loop"]); a repeating
    one is made stronger or weaker by its amount line (hz["amount"]). One counted from each note (hz["from"]):
    tone = the tone it's for (each note has its own), else from the latest note start (note_beats); with a sustain
    point: sustained. The MOD tab's links to it (hz["mod"]) then move it by their sources (mod_value), kept within
    0..1 (one to an effect with no line: live gave it one where it does nothing, see MOD_NEED_LINE). sources = a
    dict keeping each source's values for this beat and tone (the same run's effects share them)."""
    pts = (hz.get("fx") or {}).get(name)
    if not pts:
        return np.zeros(np.shape(beat))
    every = (hz.get("loop") or {}).get(name)
    mode = (hz.get("from") or {}).get(name) if every else None
    at = (hz.get("sustain") or {}).get(name) if mode == "note" else None
    span = note_span(hz, np.asarray(beat, float), tone) if at is not None else None
    timed = timed_line(hz, name, beat, tone)
    if timed is not None:  # (its time knobs moved by the MOD tab)
        v = timed
    elif span is not None:
        s0, end = span
        v = sustained(pts, every, at, np.asarray(beat, float) - s0, np.asarray(end) - s0)
    elif mode:
        v = line_at(pts, note_beats(hz, name, beat, tone), every if mode == "restart" else None)
    else:
        v = line_at(pts, beat, every)
    amount = (hz.get("amount") or {}).get(name) if every else None
    if amount:
        still = NEUTRAL.get(name, 0.0)
        v = still + (v - still) * line_at(amount, beat)
    moved = mod_turn(hz, (name, "wave") if name in WAVES else (name,), beat, tone, sources, v)
    return v if moved is None else np.clip(moved, 0.0, 1.0)


def mod_turn(hz, targets, beat, tone=None, sources=None, start=0.0):
    """start moved by the MOD tab's links to these targets at beat (an array) for tone's note: amount x source
    added on, link by link (both ways: round the middle), or None when none is linked. sources = as fx_at's."""
    turn = None
    for link in (hz.get("mod") or {}).get("links", ()):
        if link["to"] in targets:
            sources = {} if sources is None else sources
            if link["from"] not in sources:
                sources[link["from"]] = mod_value(hz, link["from"], beat, tone)
            s = sources[link["from"]]
            turn = (start if turn is None else turn) + link["amount"] * (2.0 * s - 1.0 if link.get("bipolar") else s)
    return turn


def setting_base(hz, target):
    """Where a knob of MOD_SETTINGS is set (its own value, before the MOD tab moves it), or None while it does
    nothing: no Tremolo / Sweep line (Depth, Key track), under 3 voices (Blend), OSC B off or with no waveform
    (Shape), another Mode or the oscillator off (a Mode's knobs), the Voice box off (Glide, Curve); a time knob
    whose line was drawn otherwise."""
    if target in ADSR_KNOBS and "volume" in (hz.get("bypass") or {}).get("boxes", ()):  # (the Volume box off)
        return None
    if target in TIMED_KNOBS:  # (a time knob: while its line is as the knobs make it)
        got = TIMED[TIMED_KNOBS[target]][1](hz)
        return got[target] if got else None
    return plain_base(hz, target)


def plain_base(hz, target):
    """setting_base of a knob read as it's set (not from a line)."""
    fx, lfo, osc = hz.get("fx") or {}, hz.get("lfo") or {}, hz.get("osc2") or {}
    if target in MOD_RACK:  # (an Effects tab effect's: while it's there and on)
        kind, key = MOD_RACK[target]
        e = rack_on(hz, kind)
        return e[key] if e else None
    if target in ("glide", "curve"):  # (a glide is worked out once, at the note's start: tone_runs)
        if "voice" in (hz.get("bypass") or {}).get("boxes", ()):
            return None
        return (hz.get("voice") or {}).get(target, 0.0 if target == "glide" else GLIDE_CURVE)
    if target in ("tremolo_wait", "tremolo_rise"):
        return lfo.get(target, 0.0) if "tremolo" in fx else None
    if target == "tremolo_depth":
        return lfo.get("tremolo_depth", TREMOLO_DEPTH) if "tremolo" in fx else None
    if target == "vibrato_rate":
        return lfo.get("vibrato_rate", VIBRATO_RATE) if "vibrato" in fx else None
    if target in ARP_KNOBS:  # (while the Arpeggio is on)
        arp = hz.get("arp") or {}
        return arp.get(target[4:], 0.0) if arp else None
    if target in LFO_RATES:
        return (hz.get("mod") or mod_start())["lfo"][MOD_LFOS.index(LFO_RATES[target])]["rate"]
    if target == "sweep_track":
        return lfo.get("sweep_track", 0.0) if "sweep" in fx else None
    if target == "blend":
        voice = hz.get("voice") or {}
        return voice.get("blend", BLEND) if voice.get("voices", 1) >= 3 else None
    if target in ("detune", "random"):  # (Detune: 2 voices or more)
        voice = hz.get("voice") or {}
        if "voice" in (hz.get("bypass") or {}).get("boxes", ()):
            return None
        if target == "detune":
            return voice["detune"] if voice.get("voices", 1) >= 2 else None
        return voice.get("random", 0.0)
    if target.startswith("osc2_"):
        key = target[5:]
        if not osc or key == "shape" and osc["wave"] == "none":
            return None
        if key in OSC2:
            return osc[key]
        mode = osc.get("mode") or {}
    else:
        key, mode = target, ({} if osc.get("a_off") else hz.get("mode") or {})
    kind, _, setting = key.partition("_")
    return mode.get(setting) if mode.get("kind") == kind else None


def setting_at(hz, target, base, beat, tone=None, sources=None):
    """A MOD_SETTINGS knob's value at beat (an array) for tone's note: base (setting_base) moved by the links to it,
    along the knob's turn (as the lines: amount 1 = the whole turn), kept within its ends."""
    turn = mod_turn(hz, (target,), beat, tone, sources)
    if turn is None:
        return np.full(np.shape(beat), float(base))
    return turned_setting(target, base, turn)


def turned_setting(target, base, turn):
    """A MOD_SETTINGS knob set at base, turned that much more (0..1 = its whole turn), kept within its ends."""
    lo, hi, most = MOD_SETTINGS[target]
    if most:  # (a curved knob: points at the square root of how far up it is)
        v = most * np.maximum(0.0, math.sqrt(max(0.0, base) / most) + turn) ** 2
    else:
        v = base + turn * (hi - lo)
    return np.clip(v, lo, hi)


def knob_adsr(hz):
    """The Volume line as the Volume box's knobs make it (adsr_line, once per note with its sustain point):
    {attack, decay, sustain, release}; full all along (or none) = (0, 0, 1, 0); None when it was drawn otherwise."""
    plain = {"attack": 0.0, "decay": 0.0, "sustain": 1.0, "release": 0.0}
    pts = (hz.get("fx") or {}).get("volume")
    if not pts or "volume" not in (hz.get("loop") or {}) and all(p[1] == 1.0 for p in pts):
        return plain if "volume" not in (hz.get("amount") or {}) else None
    at = (hz.get("sustain") or {}).get("volume")
    if (at is None or (hz.get("from") or {}).get("volume") != "note" or "volume" in (hz.get("fit") or ())
            or "volume" in (hz.get("amount") or {})):
        return None
    attack = pts[1][0] if len(pts) > 1 and pts[0][1] == 0.0 and list(pts[0][2:]) == [-FAST] else 0.0
    attack = min(max(0.0, attack), at)
    got = {"attack": attack, "decay": at - attack, "sustain": float(line_at(pts, at)),
           "release": pts[-1][0] - at if pts[-1][0] > at + 1e-9 else 0.0}
    return got if same_points(adsr_line(**got)[0], pts) else None


def stage_sums(length, dt):
    """For a stage whose length (beats) is read at every step of a grid dt apart: how far through a stage of that
    length the time from the grid's start has come (adding dt / length step by step: a length moving while the
    stage runs makes it go faster or slower). A length of 0 = done at once."""
    step = dt / np.maximum(np.asarray(length, float), 1e-12)
    return np.concatenate([[0.0], np.cumsum(step[:-1])])


def stage_end(sums, g, t0):
    """When a stage starting at t0 ends (sums = stage_sums over the grid g), inf when it never does."""
    want = float(np.interp(t0, g, sums)) + 1.0
    return float(np.interp(want, sums, g)) if want <= sums[-1] else math.inf


def knob_bend(hz, name, key):
    """The Pitch / Sweep line as its box's knobs make it (pitch_line, sweep_line: from `start` to `end`, fast first, in
    `key` beats, once per note): {key, start, end}, or None (no such line, drawn otherwise)."""
    pts = (hz.get("fx") or {}).get(name)
    if (not pts or len(pts) != 2 or list(pts[0][2:]) != [FAST] or len(pts[1]) > 2 or pts[0][0] != 0.0
            or (hz.get("from") or {}).get(name) != "note" or name in (hz.get("fit") or ())
            or name in (hz.get("sustain") or {}) or name in (hz.get("amount") or {})
            or abs((hz.get("loop") or {}).get(name, 0.0) - max(LOOP[0], pts[1][0])) > 1e-9):
        return None
    return {key: pts[1][0], "start": pts[0][1], "end": pts[1][1]}


def knob_vibrato(hz):
    """The Vibrato line as its knobs make it (none for Delay = vibrato_wait beats from each note's start, then coming
    in over Rise = vibrato_delay beats, linear): {vibrato_wait, vibrato_delay, depth}, or None (drawn otherwise)."""
    pts = (hz.get("fx") or {}).get("vibrato")
    every = (hz.get("loop") or {}).get("vibrato")
    if (not pts or any(len(p) > 2 for p in pts) or name_bent(hz, "vibrato")
            or (every is not None) == (len(pts) == 1)):
        return None
    if len(pts) == 1:  # (all along)
        return {"vibrato_wait": 0.0, "vibrato_delay": 0.0, "depth": pts[0][1]}
    if ((hz.get("from") or {}).get("vibrato") != "note" or list(pts[0]) != [0.0, 0.0]
            or abs(every - max(LOOP[0], pts[-1][0])) > 1e-9):
        return None
    if len(pts) == 2:
        return {"vibrato_wait": 0.0, "vibrato_delay": pts[1][0], "depth": pts[1][1]}
    if len(pts) == 3 and pts[1][1] == 0.0:
        return {"vibrato_wait": pts[1][0], "vibrato_delay": pts[2][0] - pts[1][0], "depth": pts[2][1]}
    return None


def name_bent(hz, name):
    """An effect's line stretched over each note, with a sustain point or an amount line (no knob makes those)."""
    return (name in (hz.get("fit") or ()) or name in (hz.get("sustain") or {})
            or name in (hz.get("amount") or {}))


def knobs_set(*knobs):
    """A TIMED reader for knobs read as they're set: {knob: value}, or None while one does nothing (plain_base)."""
    def read(hz):
        got = {k: plain_base(hz, k) for k in knobs}
        return None if None in got.values() else got
    return read


TIMED = {"volume": (TIMED_NAMES["volume"], knob_adsr),
         "pitch": (TIMED_NAMES["pitch"], lambda hz: knob_bend(hz, "pitch", "time")),
         "sweep": (TIMED_NAMES["sweep"], lambda hz: knob_bend(hz, "sweep", "sweep_time")),
         "vibrato": (TIMED_NAMES["vibrato"], knob_vibrato),
         **{name: (knobs, knobs_set(*knobs)) for name, knobs in TIMED_NAMES.items() if name.endswith("_in")}}


def osc2_most(hz):
    """OSC B's tone from the note's, in keys, at the highest the MOD tab's links can take it."""
    osc = dict(hz["osc2"])
    for name in OSC2_TUNE:
        if any(link["to"] == name for link in (hz.get("mod") or {}).get("links", ())):
            v = setting_most(hz, name, osc[name[5:]])
            osc[name[5:]] = v if name == "osc2_fine" else float(round(v))
    return osc2_shift(osc)


def longest_fall(hz):
    """Beats the sound goes on after a note at most (the falls of the lines with a sustain point; a Volume Release
    the MOD tab moves: as long as it can get)."""
    fall = max((hz["loop"][name] - at for name, at in (hz.get("sustain") or {}).items()), default=0.0)
    if any(link["to"] == "release" for link in (hz.get("mod") or {}).get("links", ())) and setting_base(hz, "release")\
            is not None:
        fall = max(fall, setting_most(hz, "release", knob_adsr(hz)["release"]))
    return fall


def timed_line(hz, name, beat, tone):
    """An effect's line played once per note whose time knobs the MOD tab moves (TIMED: the Volume box's ADSR, Pitch
    Time, Sweep Time, Vibrato Delay / Rise), at beat (an array) for tone's note, or None (nothing moves them; drawn
    otherwise; no tone: as drawn). Worked out on a grid from the note's start: each stage as long as its knob says at
    every step (as a synth's envelope, a running stage gets shorter or longer)."""
    if tone is None or name not in TIMED:
        return None
    knobs, read = TIMED[name]
    linked = {link["to"] for link in (hz.get("mod") or {}).get("links", ()) if link["to"] in knobs}
    e = read(hz) if linked else None
    if e is None:
        return None
    g, v = cached(hz, ("timed", name, tone.get("id")), lambda: timed_grid(hz, name, e, linked, tone))
    return np.interp(np.asarray(beat, float), g, v)


def timed_grid(hz, name, e, linked, tone):
    """timed_line's (grid beats, the line's value there), from the note's start past its longest fall."""
    s0, end = note_span(hz, 0.0, tone)
    s0, end = float(s0), float(end)
    span = end - s0 + cached(hz, "fall", lambda: longest_fall(hz)) + 2 * TIME_STEP
    dt = max(TIME_STEP, span / 100000)
    g = s0 + np.arange(int(span / dt) + 2) * dt
    sources = {}

    def stage(key, t0, stays=False):
        """A stage of knob `key` starting at t0: (how far through it, 0..1 at each step; when it ends). A length of 0
        = over at once, or (stays: FM's Time) never moving."""
        if e[key] <= 0 and key not in linked or t0 == math.inf:
            return ((g < -math.inf) if stays else (g >= t0)).astype(float), (math.inf if stays else t0)
        length = setting_at(hz, key, e[key], g, tone, sources) if key in linked else np.full(len(g), e[key])
        if stays:
            length = np.where(length > 0, length, math.inf)
        sums = stage_sums(length, dt)
        return np.clip(sums - np.interp(min(t0, g[-1]), g, sums), 0.0, 1.0), stage_end(sums, g, t0)

    if name.endswith("_in"):  # (KeyGrid's: how far through, 0..1)
        if name == "tremolo_in":  # (none for Delay, then coming in over Rise)
            _, tw = stage("tremolo_wait", s0)
            return g, stage("tremolo_rise", tw)[0]
        return g, stage(TIMED[name][0][0], s0, stays=name.endswith("fm_in"))[0]
    if name in ("pitch", "sweep"):  # (from start to end, fast first)
        p, _ = stage(next(iter(TIMED[name][0])), s0)
        return g, e["start"] + (e["end"] - e["start"]) * (1.0 - (1.0 - p) ** 2)
    if name == "vibrato":  # (none, then coming in)
        _, tw = stage("vibrato_wait", s0)
        p, _ = stage("vibrato_delay", tw)
        return g, e["depth"] * p
    s = e["sustain"]  # (the Volume: ADSR)
    top = 1.0 if e["decay"] > 0 or "decay" in linked else s  # (no decay: the rise goes straight to the sustain)
    pa, ta = stage("attack", s0)
    pd, td = stage("decay", ta)
    held = np.where(g < ta, top * pa ** 2, np.where(g < td, 1.0 + (s - 1.0) * (1.0 - (1.0 - pd) ** 2), s))
    left = float(np.interp(end, g, held))  # (where it got when the note ends: the fall starts from there)
    pr, _ = stage("release", end)
    fell = np.clip(s * (1.0 - pr) ** 2 + (left - s) * (1.0 - pr), 0.0, 1.0)
    return g, np.where(g < end, held, fell)


def setting_most(hz, target, base):
    """The highest a MOD_SETTINGS knob set at base can be taken by its links (every source at its fullest; a link
    turning it down only adds nothing, one both ways its upward half)."""
    reach = sum(abs(link["amount"]) if link.get("bipolar") else max(0.0, link["amount"])
                for link in (hz.get("mod") or {}).get("links", ()) if link["to"] == target)
    return float(turned_setting(target, base, reach))


def mod_value(hz, src, beat, tone=None):
    """A MOD tab source's value at beat (an array, from the shape's left edge), 0..1 (see MOD_SOURCES): those counted
    from each note go by tone's note (chain of slides), or (tone None) the latest one to start (note_span)."""
    beat = np.asarray(beat, float)
    mod = hz["mod"]
    if src in ("velocity", "note"):
        def of(n):
            return (n.get("level", 1.0) if src == "velocity"
                    else min(1.0, max(0.0, n.get("played", pitch(n)) / 127.0)))
        if tone is not None:
            return np.full(beat.shape, float(of(tone)))
        tones = sorted(hz.get("tones") or (), key=lambda n: (n["t"], n["len"]))
        if not tones:
            return np.full(beat.shape, 1.0 if src == "velocity" else hz.get("key", HZ_DEFAULTS["key"]) / 127.0)
        # (no tone: the newest note's, as a synth's effects after its voices; together: the longest)
        i = np.searchsorted(np.array([n["t"] for n in tones]), beat, side="right") - 1
        return np.array([float(of(n)) for n in tones])[np.clip(i, 0, len(tones) - 1)]
    span = note_span(hz, beat, tone)
    s0, end = span if span is not None else (0.0, 0.0)
    if src in MOD_ENVS:
        return env_value(mod["env"][MOD_ENVS.index(src)], beat - s0, np.asarray(end) - s0)
    lfo = mod["lfo"][MOD_LFOS.index(src)]
    if any(link["to"] == f"{src}_rate" for link in mod.get("links", ())):
        return moved_lfo(hz, src, beat, tone, s0)
    if lfo["rate"] <= 0:  # (stopped: where a wave starts)
        return np.full(beat.shape, float(line_at(loop_shape(lfo["shape"], 1.0, seed=MOD_LFOS.index(src)), 0.0)))
    every = 1.0 / lfo["rate"]
    pts = cached(hz, ("mod", src), lambda: loop_shape(lfo["shape"], every, seed=MOD_LFOS.index(src)))
    if lfo["mode"] == "free":  # (counted late by "phase": the shape's left edge moved, the song's beats kept)
        return line_at(pts, beat - mod.get("phase", 0.0), every)
    u = np.maximum(0.0, beat - s0)
    return line_at(pts, u, every) if lfo["mode"] == "restart" else line_at(pts, np.minimum(u, every))


def moved_lfo(hz, src, beat, tone, s0):
    """mod_value of an LFO whose Rate the MOD tab moves: its waves added up step by step from its note's start (s0;
    no tone: from the shape's start), so a Rate moving makes it go faster or slower from where it is, never jump.
    Free: as if it ran at the Rate it starts with until then. The Rate's sources are read with no LFO Rate moved (two
    LFOs can't move each other round and round)."""
    mod = hz["mod"]
    i = MOD_LFOS.index(src)
    lfo = mod["lfo"][i]
    s0 = np.broadcast_to(np.asarray(s0, float), beat.shape)
    start = float(s0.min()) if tone is not None and beat.size else 0.0
    end = max(start, float(beat.max())) if beat.size else start
    dt = max(TIME_STEP, (end - start) / 100000)
    g = start + np.arange(int((end - start) / dt) + 2) * dt
    plain = dict(hz, mod=dict(mod, links=[link for link in mod["links"] if link["to"] not in LFO_RATES]))
    sources = {link["from"]: mod_value(plain, link["from"], g, tone) for link in mod["links"]
               if link["to"] == f"{src}_rate"}
    rate = setting_at(hz, f"{src}_rate", lfo["rate"], g, tone, sources)
    turns = np.concatenate([[0.0], np.cumsum(rate[:-1] * dt)])
    at = np.interp(beat, g, turns)
    if lfo["mode"] == "free":  # (counted late by "phase", as mod_value's)
        at = at + rate[0] * (start - mod.get("phase", 0.0))
    else:
        at = np.maximum(0.0, at - np.interp(s0, g, turns))
    pts = cached(hz, ("mod1", src), lambda: loop_shape(lfo["shape"], 1.0, seed=i))
    return line_at(pts, np.minimum(at, 1.0)) if lfo["mode"] == "once" else line_at(pts, at, 1.0)
