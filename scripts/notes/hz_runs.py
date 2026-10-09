"""Hz bass tones into repeats: the lines as heard (live), each tone's run of waves (held part, slides, glides, falls
after the note), Auto / fixed / mixed gates, and the red line (heard)."""

import bisect
import functools
import json
import math

import numpy as np

from notes.hz_settings import (ADSR_KNOBS, GLIDE_CURVE, MOD_BOXES, MOD_NEED_LINE, NEUTRAL, VIBRATO,
                               VIBRATO_RATE, bend_range, hz_of)
from notes.hz_lines import adsr_line
from notes.hz_glide import cached, chains, glide, glide_left, legato_links, links, note_span, pitch, slide_part
from notes.hz_modulate import fx_at, longest_fall, plain_base, rack_tail, setting_at
from notes.hz_arp import arpeggiated


def live(hz, left=0.0):
    """hz as it's heard: without the effects switched off (Bypass), its tones as the Arpeggio box plays them (left =
    the shape's left edge: the Random pattern counts from the song's start)."""
    if hz.get("arp") and hz.get("tones"):  # (taken out once played, so live of live is the same)
        arp = hz["arp"]
        hz = {k: v for k, v in hz.items() if k not in ("arp", "_memo")}  # (other tones: nothing cached holds)
        hz["tones"] = arpeggiated(hz["tones"], arp, left, hz)
    fx = hz.get("fx") or {}
    boxes_off = (hz.get("bypass") or {}).get("boxes", ())
    if ("volume" not in fx and "volume" not in boxes_off and any(
            link["to"] in ADSR_KNOBS for link in (hz.get("mod") or {}).get("links", ()))):
        # (the Volume knobs' times moved with no Volume line: as the knobs have it, full all along)
        pts, at, every = adsr_line(0.0, 0.0, 1.0, 0.0)
        hz = {k: v for k, v in hz.items() if k != "_memo"}
        hz.update(fx=dict(fx, volume=pts), loop=dict(hz.get("loop") or {}, volume=every),
                  sustain=dict(hz.get("sustain") or {}, volume=at), **{"from": dict(hz.get("from") or {}, volume="note")})
        fx = hz["fx"]
    missing = [link["to"] for link in (hz.get("mod") or {}).get("links", ()) if link["to"] in MOD_BOXES
               and link["to"] not in fx and link["to"] not in MOD_NEED_LINE and MOD_BOXES[link["to"]] not in boxes_off]
    if missing:  # (the MOD tab moves them from where they do nothing)
        hz = dict(hz, fx=dict(fx, **{name: [[0.0, NEUTRAL.get(name, 0.0)]] for name in missing}))
        hz.pop("_memo", None)
    off = hz.get("off")
    if not off:
        return hz
    out = {k: v for k, v in hz.items() if k not in ("fx", "loop", "off", "amount", "from", "fit", "sustain")}
    fx = {k: v for k, v in (hz.get("fx") or {}).items() if k not in off}
    loop = {k: v for k, v in (hz.get("loop") or {}).items() if k in fx}
    amount = {k: v for k, v in (hz.get("amount") or {}).items() if k in loop}
    froms = {k: v for k, v in (hz.get("from") or {}).items() if k in loop}
    fit = [k for k in hz.get("fit") or () if k in froms]
    sustain = {k: v for k, v in (hz.get("sustain") or {}).items() if k in froms}
    return dict(out, **({"fx": fx} if fx else {}), **({"loop": loop} if loop else {}),
                **({"amount": amount} if amount else {}), **({"from": froms} if froms else {}),
                **({"fit": fit} if fit else {}), **({"sustain": sustain} if sustain else {}))


def tails(hz):
    """{tone id: beats its sound goes on after its end}: with sustain points (hz["sustain"], effects switched off
    don't count) the falls play after a note ends, so the tones a chain of slides ends with sound on for the
    longest fall, also under the notes after them (like a synth's voices), cut only where a tone of the same pitch
    starts (both there would make their repeats twice as many: a higher tone)."""
    hz = live(hz)
    fall = longest_fall(hz)
    tones = hz.get("tones") or ()
    if fall <= 1e-12 or not tones:
        return {}
    joined = legato_links(hz.get("voice"), tones)
    got = chains(tones, joined)
    starts = {}
    for n in tones:
        starts.setdefault(pitch(n), []).append(n["t"])
    leaving = {a["id"] for a, _, _ in links(tones)} | {a["id"] for a, _ in joined}
    out = {}
    for n in tones:
        end = n["t"] + n["len"]
        if n["id"] in leaving or end < got[n["id"]][1] - 1e-9:  # (not where its chain ends)
            continue
        nxt = min((s for s in starts[pitch(n)] if s >= end - 1e-9), default=math.inf)
        if min(fall, nxt - end) > 1e-9:
            out[n["id"]] = min(fall, nxt - end)
    return out


def sound_span(hz):
    """How long the tones sound together, in beats (tones_span and the falls after them, tails; the Effects tab's
    echo and reverb after that, rack_tail)."""
    hz = live(hz)  # (as played: the Arpeggio box's tones)
    got, tones = tails(hz), hz.get("tones") or ()
    end = max((n["t"] + n["len"] + got.get(n["id"], 0.0) for n in tones), default=0.0)
    return end + rack_tail(hz) if tones else end


def glides(hz):
    """{tone id: [pitch it glides in from, ...]} (glide_tones)."""
    return {k: [pitch(a) for a in v] for k, v in glide_tones(hz).items()}


def glide_tones(hz):
    """{tone id: [tone it glides in from, ...]} with Glide on (hz["voice"]): a note glides in from the note(s)
    that ended last before it starts ("touching": only when they end right where it starts), like the slides made
    by hand: one to a chord, several to one, chord to chord none. Not into a note a slide reaches, nor from the same
    pitch."""
    voice = hz.get("voice") or {}
    tones = hz.get("tones") or ()
    if not (voice.get("glide") or glide_moved(hz)) or len(tones) < 2:
        return {}
    slid = {b["id"] for _, b, _ in links(tones)}
    ended = sorted(tones, key=lambda n: n["t"] + n["len"])
    ends = [n["t"] + n["len"] for n in ended]
    out, i = {}, 0
    order = sorted(tones, key=lambda n: n["t"])
    while i < len(order):
        t = order[i]["t"]
        j = i
        while j < len(order) and order[j]["t"] <= t + 1e-9:
            j += 1
        chord, i = order[i:j], j
        k = bisect.bisect_right(ends, t + 1e-9)
        if not k or (voice.get("touching") and ends[k - 1] < t - 1e-9):
            continue
        last, before = ends[k - 1], []
        while k and ends[k - 1] >= last - 1e-9:
            before.append(ended[k - 1])
            k -= 1
        if len(before) > 1 and len(chord) > 1:
            continue
        for b in chord:
            got = [a for a in before if abs(pitch(a) - pitch(b)) > 1e-9]
            if got and b["id"] not in slid:
                out[b["id"]] = got
    return out


def glide_moved(hz):
    """The MOD tab moves the Voice box's Glide (and its box is on): notes may glide even with Glide at 0."""
    return (any(link["to"] == "glide" for link in (hz.get("mod") or {}).get("links", ()))
            and plain_base(hz, "glide") is not None)


def glide_of(hz, n, key):
    """Note n's Glide time / Curve (key "glide" / "curve"): as set, or moved by the MOD tab as at its start (a glide
    is over quickly: read once, as it starts)."""
    base = plain_base(hz, key)
    if base is None:
        return 0.0 if key == "glide" else GLIDE_CURVE
    return float(setting_at(hz, key, base, np.array([n["t"]]), n)[0])


def slide_knob(hz, a):
    """The Glide curve an untouched slide leaving tone a follows (hz_glide.slide_part): the Voice box's Curve while
    Glide is on for a, else None (straight)."""
    return glide_of(hz, a, "curve") if glide_of(hz, a, "glide") > 0 else None


def wave(hz, ppq, key, limit=None, whole=None):
    """The gate, in ticks, of one wave of key's tone (hz = the shape's settings: cents, bpm, fixed). limit = Auto
    gates' threshold in cents for a tone held still: whole ticks when that's at most this far off. whole = True /
    False: whole ticks or not, whatever the rest says (a tone's own gates, own_gate)."""
    gate = ppq * hz["bpm"] / 60.0 / hz_of(key, hz["cents"])
    if whole is None:
        whole = hz.get("fixed") or (limit is not None and off_cents(gate) <= limit + 1e-9)
    return max(1.0, math.floor(gate + 0.5) if whole else gate)


def off_cents(gate):
    """How far, in cents, a gate rounded to a whole tick is off the tone of the exact gate (in ticks)."""
    whole = max(1.0, math.floor(gate + 0.5))
    return abs(1200.0 * math.log2(gate / whole))


def own_gate(n):
    """A placed tone's own gates while held (n["gate"], set in the Hz bass window): True = fixed, False = mixed,
    None = the Hz bass's."""
    return {"fixed": True, "mixed": False}.get((n or {}).get("gate"))


def held_fixed(hz, ppq, n):
    """True when tone n held still gets fixed gates (its own, the Hz bass's Fixed, or Auto's pick)."""
    own = own_gate(n)
    if own is not None:
        return own
    got = auto_state(hz, ppq, n)
    return bool(hz.get("fixed")) if got is None else got[2]


def threshold(hz, n=None):
    """Auto gates' threshold in cents for tone n (its own, or the Hz bass's), or None when the gates aren't Auto."""
    if hz.get("auto") is None:
        return None
    return (n or {}).get("auto", hz["auto"])


def auto_state(hz, ppq, n):
    """For tone n held still with Auto gates or gates of its own: (how far fixed gates would be off, in cents; the
    threshold, None for its own gates; True when it gets fixed gates). None when neither."""
    limit, own = threshold(hz, n), own_gate(n)
    if own is not None:
        limit = None
    elif limit is None:
        return None
    off = off_cents(ppq * hz["bpm"] / 60.0 / hz_of(pitch(n), hz["cents"]))
    return off, limit, own if own is not None else off <= limit + 1e-9


def auto_picks(hz, ppq):
    """Which tones get fixed gates with Auto gates (True / False each; one for a Hz bass without placed tones), or
    None when the gates aren't Auto. A threshold change that leaves these the same leaves the notes the same."""
    if hz.get("auto") is None:
        return None
    if not hz.get("tones"):
        return (off_cents(ppq * hz["bpm"] / 60.0 / hz_of(hz["key"], hz["cents"])) <= hz["auto"] + 1e-9,)
    return tuple(auto_state(hz, ppq, n)[2] for n in hz["tones"])


def tone_runs(hz, left, ppq, keys=None):
    """The repeats of the placed tones as unbroken stretches of tone: [(start ticks, the ticks their waves are over
    = the next one's start, whose: (tone, None) or (tone slid from, tone slid to))], not rounded. A tone held is
    one stretch, from where the first slide into it arrives to where the last slide out of it leaves; every slide
    is one more (two when there's a gap between its tones: nothing sounds there), its waves in step with the tone
    it leaves. A tone a chain ends with goes on for its fall (tails). Glide (glide_tones): a tone's start is one more
    stretch from each tone it glides in from, fast first, then the tone held (whose: (tone, None) for both).
    No cut wave where one goes on from another (like a synth's oscillator running on): a tone a slide, a glide or
    Legato reaches is held from the last wave of what reached it, a glide starts on the next wave's end of the
    tone it leaves, and with no held part left the slide out of a tone starts where the one into it ended. Worked
    out in time order, each stretch bent by the Pitch line as it's made, so these hold with it too. keys = one more
    bend (keys up at beats for a tone: OSC B's tune moved, KeyGrid.osc2_keys), made the same way."""
    tones = hz["tones"]
    ls = links(tones)
    tail = tails(hz)
    gl = glide_tones(hz)
    legato = {}
    for p, n in legato_links(hz.get("voice"), tones):
        legato.setdefault(n["id"], p)  # (several to one: the first)
    ins, outs = {}, {}
    for i, (a, b, s) in enumerate(ls):
        ins.setdefault(b["id"], []).append(min(s["in"], b["len"]))
        outs.setdefault(a["id"], []).append(i)
    pitched = "pitch" in (hz.get("fx") or {})
    bend = pitched or keys is not None
    own, made = {}, {}
    held = {}  # (tone id: its held part's waves, start, gate, end)
    carry = {}  # (tone id with no held part left: (where what reached it ended, where it reached it as placed))
    reach = {}  # (tone id: (where the first slide reaching it where its held part starts ends, that as placed))

    def run(starts, nexts, whose):
        starts, nexts = np.asarray(starts, float), np.asarray(nexts, float)
        if pitched:
            starts, nexts = bent(hz, left, ppq, starts, nexts, whose[0])
        if keys is not None:
            starts, nexts = bent(hz, left, ppq, starts, nexts, keys=lambda b: keys(b, whose[0]))
        return starts, nexts, whose

    def next_wave(p, at):  # (where tone p's sound next ends a wave at or after tick at; None when it isn't there)
        h = held.get(p["id"])
        if h is None:
            c = carry.get(p["id"])
            return c[0] if c is not None and c[0] >= at - 1e-6 else None
        starts, nexts, s, gate, e = h
        if e < at - 1e-6 or at < s:
            return None
        if not bend:
            return s + math.ceil((at - s) / gate - 1e-9) * gate
        ends = np.append(starts[:1], nexts)
        return float(ends[min(int(np.searchsorted(ends, at - 1e-6)), len(ends) - 1)])

    for n in sorted(tones, key=lambda n: (n["t"], n["t"] + n["len"])):
        me = n["id"]
        a = n["t"] + min(ins.get(me, ()), default=0.0)
        took = glide_of(hz, n, "glide") if me in gl else 0.0
        lead = None  # (where the glide / Legato into it ends, on its last wave: the tone held goes on from there)
        at = (left + n["t"]) * ppq
        if took > 0:
            s, e = at, (left + n["t"] + min(took, n["len"])) * ppq
            if n["t"] + min(took, n["len"]) >= a - 1e-9 and len(gl[me]) == 1:
                a = n["t"] + min(took, n["len"])
                lead = e
            a = max(a, n["t"] + min(took, n["len"]))
            curve = glide_of(hz, n, "curve")
            for p in gl[me]:
                k0 = pitch(p)
                t = next_wave(p, at)
                part, after, t = [], [], s if t is None else t
                while t < e:
                    part.append(t)
                    t += wave(hz, ppq, pitch(n) + (k0 - pitch(n)) * glide_left((t - s) / (e - s), curve))
                    after.append(t)
                if part:
                    got = run(part, after, (n, None))
                    own.setdefault(id(n), []).append(got)
                    t = got[1][-1]
                if lead is not None:
                    lead = t
        elif me in legato and me not in ins:
            lead = next_wave(legato[me], at)
        b = n["t"] + n["len"] - min([min(ls[i][2]["out"], n["len"]) for i in outs.get(me, ())], default=0.0)
        b += tail.get(me, 0.0)
        placed, e = (left + a) * ppq, (left + b) * ppq
        r = reach.get(me)
        s = r[0] if r is not None and abs(r[1] - placed) < 1e-6 else placed if lead is None else lead
        if b > a and s < e:
            gate = wave(hz, ppq, pitch(n), threshold(hz, n), own_gate(n))
            if gate == math.floor(gate) and s != placed:  # (whole-tick waves: on the next whole tick)
                s = math.ceil(s - 1e-9)
            starts = s + gate * np.arange(max(1, int(math.ceil((e - s) / gate))))
            got = run(starts, starts + gate, (n, None))
            own.setdefault(id(n), []).append(got)
            held[me] = (got[0], got[1], s, gate, e)
        elif s != placed:  # (what reached it goes past its end: no held part)
            carry[me] = (s, placed)
        for i in outs.get(me, ()):  # (its slides, in step with it; the tone each reaches is worked out later)
            _, c, link = ls[i]
            x0, x1, k0, k1 = glide(n, c, link)
            if x1 - x0 < 1e-12:
                continue
            knob = slide_knob(hz, n)
            s0, e0 = (left + x0) * ppq, (left + n["t"] + n["len"]) * ppq
            s1, e1 = (left + c["t"]) * ppq, (left + x1) * ppq
            t = s0
            if me in held and s0 >= held[me][2]:
                t = next_wave(n, s0)
            elif me in carry and carry[me][1] <= s0 + 1e-6:
                t = max(s0, carry[me][0])
            gap = s1 - e0 > 1e-6
            made[i] = []
            for end in (e0, e1) if gap else (e1,):
                part, after = [], []
                while t < end:
                    part.append(t)
                    t += wave(hz, ppq, k0 + (k1 - k0) * slide_part((t - s0) / (e1 - s0), link, knob))
                    after.append(t)
                if part:
                    got = run(part, after, (n, c))
                    made[i].append(got)
                    t = got[1][-1]
                t = max(t, s1)
            if c["id"] not in reach and abs((left + c["t"] + min(ins[c["id"]])) * ppq - e1) < 1e-6:
                reach[c["id"]] = (t, e1)
    out = [got for n in tones for got in own.get(id(n), ())]
    for i in range(len(ls)):
        out.extend(made.get(i, ()))
    return out


def follows(hz, items):
    """For each stretch of tone (items: (start tick, tick its last wave ends, tone, tone slid to or None), as
    tone_runs makes them): (the one its sound goes on from, or -1; True when both are in one chain of slides /
    Legato, so the effects counted from each note go on too). It goes on from one ending where it starts (a
    whole-tick held part: up to a tick later) in its own chain, or from the tone it glides in from."""
    tones = hz.get("tones") or ()
    span = cached(hz, "chains", lambda: chains(tones, legato_links(hz.get("voice"), tones)))
    gl = {k: {p["id"] for p in v} for k, v in glide_tones(hz).items()}
    order = sorted(range(len(items)), key=lambda i: items[i][1])
    ends = [items[i][1] for i in order]
    out = []
    for i, (s, _, n0, _) in enumerate(items):
        got, k = (-1, False), bisect.bisect_right(ends, s + 1e-6)
        while k > 0 and ends[k - 1] > s - 1.0 - 1e-6:
            k -= 1
            j = order[k]
            m0, m1 = items[j][2], items[j][3]
            if j == i:
                continue
            chained = span.get(m0["id"]) is not None and span.get(m0["id"]) == span.get(n0["id"])
            if chained or (m1 or m0)["id"] in gl.get(n0["id"], ()):
                got = (j, chained)
                break
        out.append(got)
    return out


def bent(hz, left, ppq, starts, nexts, tone=None, keys=None):
    """A stretch of tone's repeats (start ticks, next ones' starts) moved by the "pitch" effect: the tone goes up or
    down by the line (the Range's keys at 1 and 0), its waves shorter or longer. The repeats are spaced by adding up the
    tone over time, so a big bend keeps its timing (a repeat is where the waves so far come to a whole number).
    tone = the tone the stretch belongs to (a slide: the one it leaves), for a line counted from each note. keys =
    another bend instead of the line's: keys up at beats (OSC B's tune moved, KeyGrid.osc2_keys)."""
    t = np.append(starts, nexts[-1])
    up = ((fx_at(hz, "pitch", t / ppq - left, tone) - 0.5) * 2.0 * bend_range(hz) if keys is None
          else keys(t / ppq - left))
    f = 2.0 ** (up / 12.0)  # (how many times the tone)
    phase = np.concatenate([[0.0], np.cumsum((f[:-1] + f[1:]) / 2.0)])  # (one wave as placed = 1 at f = 1)
    n = max(1, int(math.ceil(phase[-1] - 1e-9)))
    at = np.interp(np.arange(n + 1, dtype=float), phase, t)
    past = np.arange(n + 1) > phase[-1]  # (the last one's end, past what was placed: its wave carries on)
    at[past] = t[-1] + (np.arange(n + 1)[past] - phase[-1]) * (nexts[-1] - starts[-1]) / f[-1]
    return at[:-1], at[1:]


def _limits(hz, left, ppq, starts):
    """For each repeat (start ticks, not rounded): the tick the sound it's in ends at (the end of the tones that
    touch or overlap around it, their falls included: tails)."""
    at, ends = cached(hz, ("limits", left, ppq), lambda: _spans(hz, left, ppq))
    return ends[np.maximum(np.searchsorted(at, starts, "right") - 1, 0)]


def _spans(hz, left, ppq):
    """The stretches the tones sound in together (touching or overlapping, falls included): (start ticks, a hair
    early; end ticks)."""
    spans, tail = [], tails(hz)
    for n in sorted(hz["tones"], key=lambda n: n["t"]):
        end = n["t"] + n["len"] + tail.get(n["id"], 0.0)
        if spans and n["t"] <= spans[-1][1] + 1e-9:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([n["t"], end])
    at = np.array([(left + a) * ppq for a, _ in spans]) - 1e-6
    return at, np.array([math.floor((left + b) * ppq + 0.5) for _, b in spans], np.int64)


def _whole(v):
    return np.floor(v + 0.5).astype(np.int64)


def vibrato_keys(hz, beat, tone, before=None):
    """Keys the Vibrato moves a stretch of tone's repeats at beat (an array; 0 without it), as the notes are made
    (hz_grid.KeyGrid.made: each wave VIBRATO x the line longer / shorter, its Rate counted from the start of the
    note's chain of slides; a Rate the MOD tab moves added up, on from the stretch before in its chain: before =
    (its last beat, turns there, Rate there)), for the red line only; and (last beat, turns, Rate) for the next."""
    depth = fx_at(hz, "vibrato", beat, tone) if "vibrato" in (hz.get("fx") or {}) and len(beat) else None
    if depth is None or not np.any(depth):
        return 0.0, None
    span = note_span(hz, beat, tone)
    since = beat - (tone["t"] if span is None else span[0])
    base = plain_base(hz, "vibrato_rate")
    if any(link["to"] == "vibrato_rate" for link in (hz.get("mod") or {}).get("links", ())) and base is not None:
        rate = setting_at(hz, "vibrato_rate", base, beat, tone)
        first = rate[0] * since[0] if before is None else before[1] + before[2] * (beat[0] - before[0])
        turns = first + np.concatenate([[0.0], np.cumsum(rate[:-1] * np.diff(beat))])
        last = (float(beat[-1]), float(turns[-1]), float(rate[-1]))
    else:
        turns, last = (hz.get("lfo") or {}).get("vibrato_rate", VIBRATO_RATE) * since, None
    return -12.0 * np.log2(1.0 + VIBRATO * depth * np.sin(2.0 * np.pi * turns)), last


@functools.lru_cache(maxsize=16)
def _heard(hz_json, left, ppq, bpm):
    hz = dict(json.loads(hz_json), _memo={})
    out, runs = [], list(tone_runs(hz, left, ppq))
    after = follows(hz, [(s[0], e[-1], w[0], w[1]) for s, e, w in runs])
    lasts = {}
    for i in sorted(range(len(runs)), key=lambda i: runs[i][0][0]):  # (a stretch after the one it goes on from)
        starts, nexts, whose = runs[i]
        limits = _limits(hz, left, ppq, starts)
        j, chained = after[i]
        wobble, lasts[i] = vibrato_keys(hz, starts / ppq - left, whose[0], lasts.get(j) if chained else None)
        mean = nexts - starts  # (the wave as made: with mixed gates the whole-tick ones come to this on average)
        starts, nexts = _whole(starts), _whole(nexts)
        gates = np.maximum(nexts - starts, 1)  # (whole ticks: what the PPQ lets the wave be)
        keys, mean = (69.0 + 12.0 * np.log2(ppq * bpm / 60.0 / 440.0 / g) - hz["cents"] / 100.0 + wobble
                      for g in (gates, mean))
        ends = np.minimum(nexts, limits)
        keep = ends > starts
        if keep.any():
            out.append((i, starts[keep] / ppq - left, ends[keep] / ppq - left, keys[keep], mean[keep]))
    return [got[1:] for got in sorted(out, key=lambda got: got[0])]  # (in tone_runs' order)


def heard(hz, left, ppq, bpm):
    """What the placed tones really sound like at this PPQ and BPM (the red line of the Hz bass window): for each
    unbroken stretch of tone, arrays (start, end in beats from the left edge, pitch in keys, average pitch) of its
    repeats. A repeat lasts a whole number of ticks, so its pitch is a little off the wanted one: the lower the
    PPQ, the more. Alternating ("mixed") gates take turns so that the average is the wanted tone; fixed gates are all the same, so
    there the average is each repeat's own pitch.
    The pitch is counted from the shape's own tuning (hz["cents"]), so a key's exact tone is that key."""
    return _heard(json.dumps(live(hz, left), sort_keys=True), left, ppq, float(bpm))


@functools.lru_cache(maxsize=16)
def _squares(hz_json, left, ppq):
    hz = dict(json.loads(hz_json), _memo={})
    got = [s for s, _, _ in tone_runs(hz, left, ppq)]
    if not got:
        return np.zeros((0, 2), np.int64)
    starts = np.concatenate(got)
    return _grid(starts, _limits(hz, left, ppq, starts))[0]


def _grid(starts, limits):
    """Repeats (start ticks, not rounded; the tick each one's sound ends at) -> (start, end) whole ticks in order,
    each lasting until the next one starts; and which repeat each of them is."""
    if not len(starts):
        return np.zeros((0, 2), np.int64), np.zeros(0, np.int64)
    starts = _whole(starts)
    order = np.argsort(starts, kind="stable")
    starts, limits = starts[order], limits[order]
    first = np.concatenate([[True], starts[1:] != starts[:-1]])
    at = np.flatnonzero(first)
    order = order[at]
    starts, limits = starts[at], np.maximum.reduceat(limits, at)  # (repeats of two lines on one tick: one note)
    ends = np.minimum(np.concatenate([starts[1:], limits[-1:]]), limits)
    keep = ends > starts
    out = np.column_stack([starts, ends])[keep]
    out.setflags(write=False)
    return out, order[keep]
