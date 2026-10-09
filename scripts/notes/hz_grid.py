"""Hz bass with effects: KeyGrid works out every key's own repeats and loudness (lateness, waveform hits, wave modes,
voices, OSC B, the Effects tab), all of a key's runs at once."""

import bisect
import math

import numpy as np

from notes.hz_settings import (CRUSH, FM_INDEX, FX, GROWL, HZ_DEFAULTS, MODE_AMOUNTS, MOD_RACK, MOD_SETTINGS,
                               NOT_EACH, OFF_PITCH, OSC2_TUNE, SOFT, SPEED_KNOBS, SUB, TIME_STEP, TREMOLO,
                               TREMOLO_DEPTH, VEL_FX, VIBRATO, VIBRATO_RATE, VOICES, WAH, WAVES, blend_gains, copies,
                               group_count, osc2_shift, rack_on)
from notes.hz_glide import legato_links, links, note_span, pitch
from notes.hz_lines import line_at, note_vel
from notes.hz_modulate import TIMED, fx_at, setting_at, setting_base, setting_most, timed_line
from notes.hz_runs import _grid, _limits, bent, tails, tone_runs


def compress(loud, comp):
    """A steady loudness (0..1, 1 = full) through the Effects tab's compressor (RACK): what's over its threshold cut
    to 1 / ratio of how far over (in dB), then all of it `gain` dB louder, never past full; silence stays silent.
    (Its picture; the notes get the same, but over time: KeyGrid.comp_curve.)"""
    loud = np.asarray(loud, float)
    db = 20.0 * np.log10(np.maximum(loud, 1e-12))
    cut = np.maximum(0.0, db - comp["threshold"]) * (1.0 - 1.0 / comp["ratio"])
    return np.where(loud > 0, np.minimum(1.0, 10.0 ** ((db - cut + comp["gain"]) / 20.0)), 0.0)


def silent_beside(starts, limits, quiet, osc):
    """A key row playing both oscillators: which repeats are too quiet while the other oscillator sounds there (its
    latest repeat by then is heard and not over), so they're left out before the notes are laid end to end and don't
    cut the other's notes short (OSC B at Level 0 = as if off). Arrays, one item per repeat."""
    drop = np.zeros(len(starts), bool)
    for o in (0, 1):
        mine, other = np.flatnonzero(quiet & (osc == o)), np.flatnonzero(osc != o)
        if not len(mine) or not len(other):
            continue
        other = other[np.argsort(starts[other], kind="stable")]
        at = np.searchsorted(starts[other], starts[mine], "right") - 1
        got = other[np.maximum(at, 0)]
        drop[mine] = (at >= 0) & ~quiet[got] & (starts[mine] < limits[got])
    return drop


def fall_off(since, time):
    """1 at a note's start, falling to 0 over `time` beats, fast first (time 0: stays 1). since = beats (array)."""
    since = np.asarray(since, float)
    if time <= 0:
        return np.ones(since.shape)
    return (1.0 - np.clip(since / time, 0.0, 1.0)) ** 2


def wave_hits(shapes, plain, number, since, mode):
    """The hits in each wave (repeat) of a stretch with a waveform or a wave mode (FM, Pulse width, Sync): (where,
    0..1 of the wave; loudness 0..1), arrays (repeats x hits). shapes = [(waveform function, its value for each repeat,
    on for each repeat)]; plain = the repeats with no waveform on (one hit a wave; FM makes them a sine, Pulse width
    full hits); number = each repeat's number in its stretch; since = beats from its note's start; mode = hz["mode"]
    ({} = none; its knobs may be arrays, one value each repeat; "progress" = how far through its Time each is;
    "turns" = its speed moved by the MOD tab: Pulse's waves so far, FM's wobbles so far less Ratio x the wave's
    number)."""
    part = np.arange(SUB) / SUB
    rows, kind = len(plain), mode.get("kind")
    p = np.broadcast_to(part, (rows, SUB))
    moved = mode.get("progress")  # (its Time moved by the MOD tab: how far through it each repeat is)
    if kind == "fm":  # (the place in the wave pushed back and forth; counted over the stretch so it runs on)
        depth = FM_INDEX * mode["depth"] * (fall_off(since, mode["time"]) if moved is None else (1.0 - moved) ** 2)
        at = np.asarray(number, float)[:, None] + part[None, :]
        wobble = (mode["ratio"] * at if "turns" not in mode
                  else mode["turns"][:, None] + mode["ratio"][:, None] * at)
        p = np.mod(at + depth[:, None] / (2.0 * np.pi) * np.sin(2.0 * np.pi * wobble), 1.0)
    mix = np.ones((rows, SUB))
    for fn, v, on in shapes:  # (several on one note: multiplied)
        mix *= np.where(on[:, None], (1.0 - v[:, None]) * (part == 0) + v[:, None] * fn(p), 1.0)
    if kind == "fm":
        mix[plain] = WAVES["sine"](p[plain])
    elif kind == "pulse":
        mix[plain] = 1.0
    else:
        mix[plain] = part == 0
    if kind == "pulse":
        turns = mode["turns"] if "turns" in mode else mode["rate"] * since
        width = mode["width"] + (0.5 - mode["width"]) * (1.0 - np.cos(2.0 * np.pi * turns)) / 2.0
        mix = mix * (part[None, :] < width[:, None])
    where = np.broadcast_to(part, (rows, SUB))
    if kind == "sync" and rows:  # (the wave's hits squeezed into 1 / r of it, over and over until the wave ends)
        r = (1.0 + (mode["amount"] - 1.0) * moved if moved is not None else mode["amount"] if mode["time"] <= 0
             else 1.0 + (mode["amount"] - 1.0) * np.clip(since / mode["time"], 0, 1))
        r = np.broadcast_to(np.asarray(r, float), (rows,))
        cols = np.flatnonzero((mix >= SOFT).any(axis=0))
        k = int(math.ceil(r.max() - 1e-9))
        where = (np.arange(k)[None, :, None] + part[cols][None, None, :]) / r[:, None, None]
        mix = mix[:, None, cols] * (where < 1.0 - 1e-9)
        where, mix = where.reshape(rows, -1), mix.reshape(rows, -1)
    return where, mix


class KeyGrid:
    """The repeats of a Hz bass with effects: every key has its own (squares(key)), and how much of
    the shape's velocity each of them gets (factor)."""

    def __init__(self, hz, left, ppq, lo, n):
        hz = dict(hz, _memo={})  # (its own: what's worked out once for all the notes, cached)
        self.lo, self.n, self.got, self.mine = lo, max(1, n), {}, {}
        self.copies, self.same = copies(hz), bool((hz.get("voice") or {}).get("same"))
        self.gains = blend_gains(hz)  # (each copy's loudness: Blend)
        self.random = (hz.get("voice") or {}).get("random", 0.0)  # (Random start: see starting_points)
        self.left = left
        self.mode, self.ppq = hz.get("mode") or {}, ppq
        self.chorus, self.echo, self.reverb, self.flanger, self.comp = (
            rack_on(hz, k) for k in ("chorus", "echo", "reverb", "flanger", "compressor"))
        self.order = [e["kind"] for e in hz.get("rack") or () if not e.get("off")]  # (the compressor's place counts)
        lfo = hz.get("lfo") or {}
        self.trem_in = (lfo.get("tremolo_wait", 0.0), lfo.get("tremolo_rise", 0.0))  # (beats: see LFO)
        self.track = lfo.get("sweep_track", 0.0)  # (Key track: the Sweep follows each note's pitch)
        self.osc2 = hz.get("osc2") or None  # (OSC B: see the docstring)
        self.modes = [self.mode, (self.osc2 or {}).get("mode") or {}]  # (each oscillator's own Mode)
        self.a_off = bool((self.osc2 or {}).get("a_off"))  # (OSC A switched off: only OSC B sounds)
        # (the knobs that aren't lines the MOD tab moves, each run's values worked out in made_runs; setting_at)
        self.hz = hz
        self.moved = {link["to"] for link in (hz.get("mod") or {}).get("links", ())
                      if link["to"] in MOD_SETTINGS and link["to"] not in NOT_EACH
                      and setting_base(hz, link["to"]) is not None}
        self.turned = sorted(name + "_turns" for name in self.moved if name in SPEED_KNOBS)  # (speeds: added up)
        # (the Tremolo's Delay / Rise and the Modes' Time moved: how far through them each repeat is, timed_line)
        self.linked = linked = {link["to"] for link in (hz.get("mod") or {}).get("links", ())}
        self.timed = sorted(name for name, (knobs, read) in TIMED.items()
                            if name.endswith("_in") and linked & set(knobs) and read(hz) is not None)
        self.tune = [name for name in OSC2_TUNE if name in linked] if self.osc2 else []  # (OSC B's tune moved)
        n = len(self.copies)
        self.middle = np.isin(np.arange(n), ((n - 1) // 2, n // 2))  # (Blend: the middle copies)
        self.runs = [] if self.a_off else self.made_runs(hz, left, ppq, 0)
        if self.osc2:  # (its notes moved by its tune: everything counted from each note stays as the notes have it)
            shift = 100.0 * osc2_shift(self.osc2)
            # (played: the note's own pitch, for the MOD tab's Note)
            moved = dict(hz, _memo={}, tones=[dict(n, cents=n.get("cents", 0.0) + shift, played=pitch(n))
                                              for n in hz["tones"]])
            if abs(self.osc2["fine"]) > 1e-9:  # (Fine: alternating gates, else whole-tick ones would round it away)
                moved = {k: v for k, v in moved.items() if k not in ("fixed", "auto")}
                moved["tones"] = [{k: v for k, v in n.items() if k not in ("gate", "auto")} for n in moved["tones"]]
            self.runs += self.made_runs(moved, left, ppq, 1)
        if self.reverb:  # (each oscillator's tail of its own)
            self.runs += [r for o in (0, 1) for r in self.reverb_runs(hz, left, ppq, [r for r in self.runs
                                                                                        if r["osc"] == o])]
        self.shaped = (any(r["has_" + name].any() for r in self.runs for name in WAVES)
                       or any(m.get("kind") in ("fm", "pulse", "sync") for m in self.modes))
        self.loud = (self.shaped or any(name in (hz.get("fx") or ()) for name in VEL_FX)
                     or bool(self.echo or self.reverb or self.comp) or bool((self.gains != 1.0).any())
                     or "blend" in self.moved or any(np.any(r["level"] != 1.0) for r in self.runs)
                     or any(np.any(r["own"] > 0) for r in self.runs) or bool(hz.get("loud")))
        self.pack()
        self.rack_turns = {name: self.added_up(name) for name in ("chorus_rate", "flanger_rate") if name in self.moved}
        self.starting = self.starting_points() if self.random or "random" in self.moved else None
        self.squeeze = self.comp_curve() if self.comp else None  # (the compressor's turn-down over time)

    def made_runs(self, hz, left, ppq, osc):
        """One oscillator's runs (OSC A = 0: the notes as placed; OSC B = 1: hz with its tones moved by its tune):
        each stretch of tone's repeats with every value they need."""
        runs = []
        tail = tails(hz)
        home = hz.get("key", HZ_DEFAULTS["key"]) + hz.get("cents", 0.0) / 100.0  # (Key track: from the Hz bass's tone)
        shift = osc2_shift(self.osc2) if osc else 0.0
        b = self.osc2 if osc else None
        for starts, nexts, whose in tone_runs(hz, left, ppq):
            n0 = whose[0]
            if osc and self.tune:  # (OSC B's tune moved: its tone bent from the note's, as the Pitch line bends it)
                starts, nexts = bent(hz, left, ppq, starts, nexts, keys=lambda b: self.osc2_keys(hz, b, n0))
            beat = starts / ppq - left
            run = {"starts": starts, "waves": nexts - starts, "number": np.arange(len(starts)), "beat": beat,
                   "limits": _limits(hz, left, ppq, starts),
                   # (a repeat moved past its own tone's end, its fall included, is left out: the next tone may
                   # touch it)
                   "until": (left + n0["t"] + n0["len"] + tail.get(n0["id"], 0.0)) * ppq if whose[1] is None
                   else np.inf}
            fx = hz.get("fx") or {}
            sources = {}  # (the MOD tab's sources: worked out once for the run)
            for name in FX:  # (each tone counts from its own start, like a synth's voice)
                run[name] = fx_at(hz, name, beat, n0, sources)
            mono = {}  # (the Effects tab's: from the newest note, for the whole sound)
            for name in self.moved:  # (the MOD tab's knobs that aren't lines: from each note too)
                run[name] = (setting_at(hz, name, setting_base(hz, name), beat, None, mono) if name in MOD_RACK
                             else setting_at(hz, name, setting_base(hz, name), beat, n0, sources))
            for name in self.timed:
                run[name] = timed_line(hz, name, beat, n0)
            run["swept"] = np.full(len(beat), "sweep" in fx)  # (sweep at 0 = the bump on the lowest key)
            run["has_volume"] = np.full(len(beat), "volume" in fx)
            for name in WAVES:  # (a waveform at 0 = the plain tone; without any: the plain tone too)
                run["has_" + name] = np.full(len(beat), name in fx if b is None else name == b["wave"])
                if b is not None:  # (OSC B: its own waveform, not the lines' (OSC A's))
                    shape = run["osc2_shape"] if "osc2_shape" in self.moved else np.full(len(beat), b["shape"])
                    run[name] = shape if name == b["wave"] else np.zeros(len(beat))
            if b is not None:
                run["octave"] = np.zeros(len(beat))  # (Octave below is OSC A's)
            run["groups"] = group_count(run["groups"])
            run["turns"] = np.concatenate([[0.0], np.cumsum(run["tremolo"] * TREMOLO * run["waves"] / ppq)[:-1]])
            lfo = hz.get("lfo") or {}
            run["vib_rate"] = lfo.get("vibrato_rate", VIBRATO_RATE)
            depth = lfo.get("tremolo_depth")  # (as it was without one: the very same numbers)
            run["trem"] = (0.1, TREMOLO_DEPTH) if depth is None else (1.0 - depth, depth)
            span = note_span(hz, beat, n0)  # (beats from its note's start, a chain's: the wave modes)
            run["since"] = beat - (n0["t"] if span is None else span[0])
            run["trem_since"] = run["since"]  # (the tremolo's Delay / Rise: a reverb tail keeps it as at the end)
            if "random" in self.moved:  # (Random start moved: read once, as its note (chain of slides) starts)
                head = run["beat"][:1] - run["since"][:1]
                run["random"] = np.full(len(beat), float(setting_at(hz, "random", setting_base(hz, "random"), head,
                                                                    n0, {})[0]))
            for k in self.turned:  # (a speed the MOD tab moves: its waves added up repeat by repeat, SPEED_KNOBS)
                r = run[k[:-6]]
                if k.endswith("fm_ratio_turns"):  # (wobbles per wave, less Ratio x the wave's number: wave_hits)
                    run[k] = np.concatenate([[0.0], np.cumsum(r[:-1])]) - r * run["number"]
                else:  # (times a beat: the vibrato from the stretch's start, Pulse from the note's)
                    first = r[0] * run["since"][0] if len(r) and k != "vibrato_rate_turns" else 0.0
                    run[k] = first + np.concatenate([[0.0], np.cumsum(r[:-1] * np.diff(beat))])
            run["tone"], run["held"] = n0, whose[1] is None
            run["track"] = pitch(n0) - shift - home  # (keys the note is above the Hz bass's own tone)
            # (OSC B's Level x the Arpeggio step's loudness)
            level = run["osc2_level"] if b is not None and "osc2_level" in self.moved else (
                b["level"] if b is not None else 1.0)
            run["osc"], run["level"] = osc, level * n0.get("level", 1.0)
            own = note_vel(n0, beat)  # (the note's own velocity from its loudness line, of 127; 0 = the Hz bass's)
            run["own"] = own / 127.0 if own is not None else 0.0
            n1 = whose[1]
            if n1 is not None and (n0.get("vel") or n1.get("vel")):  # (a slide: from the note slid to's start on,
                into = beat >= n1["t"] - 1e-9  # its line, as a synth reads the velocity at its key press)
                got = note_vel(n1, beat)
                run["own"] = np.where(into, got / 127.0 if got is not None else 0.0, run["own"])
            run["layer"] = line_at(hz["loud"], beat) if hz.get("loud") else 1.0  # (the layer's loudness line)
            runs.append(run)
        return runs

    def osc2_keys(self, hz, beat, tone):
        """Keys OSC B's tone is moved from where its knobs set it at beat (an array) for tone's note, while the MOD
        tab moves its Octave / Semi (whole octaves / keys, as their knobs) or Fine."""
        sources, got = {}, {}
        for name in OSC2_TUNE:
            k = name[5:]
            v = setting_at(hz, name, self.osc2[k], beat, tone, sources) if name in self.tune else self.osc2[k]
            got[k] = v if k == "fine" else np.round(v)
        return osc2_shift(got) - osc2_shift(self.osc2)

    def starting_points(self):
        """Random start: where each Voice copy's waves start in each note (runs x VOICES, 0..1 of a wave), as a
        synth's random phase: new at every note, kept through its slides (drawn from the beat in the song its chain
        of slides starts at, so the same every time the notes are made, and after a Split or a moved left edge)."""
        f, at = self.flat, np.minimum(self.each["offsets"], len(self.flat["beat"]) - 1)  # (a run with none: any)
        heads = self.left + f["beat"][at] - f["since"][at] if len(f["beat"]) else np.zeros(len(self.runs))
        drawn = {}
        out = np.zeros((len(self.runs), VOICES))
        for i, h in enumerate(heads):
            seed = int(round(float(h) * 65536)) % (2 ** 32)
            if seed not in drawn:  # (OSC B's copies: the next ones drawn, OSC A's as they always were)
                drawn[seed] = np.random.default_rng([seed, 7]).random(2 * VOICES)
            o = self.runs[i]["osc"]
            out[i] = drawn[seed][o * VOICES:(o + 1) * VOICES]
        return out

    def pack(self):
        """Every run's numbers end to end (self.flat: one array each, each repeat's value), and each run's own in
        self.each, so made() works out all of a key's repeats at once instead of run by run (an arpeggio makes
        hundreds of runs; one by one, 128 keys took seconds)."""
        runs = self.runs
        n = np.array([len(r["starts"]) for r in runs], np.int64)
        names = ["starts", "waves", "beat", "limits", "since", "trem_since", "turns", "swept", "has_volume", "groups",
                 "track", "level", "own", "layer", *FX, *sorted(self.moved), *self.timed, *self.turned,
                 *("has_" + name for name in WAVES)]
        self.flat = {k: np.concatenate([np.broadcast_to(np.asarray(r[k]), (len(r["starts"]),)) for r in runs])
                     if runs else np.zeros(0) for k in names}
        self.flat["tail_u"] = np.concatenate([r["tail_u"] if "tail_u" in r else np.zeros(len(r["starts"]))
                                              for r in runs]) if runs else np.zeros(0)
        offsets = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64)
        last = np.maximum(offsets + n - 1, 0)
        f = self.flat
        self.each = {"n": n, "offsets": offsets,
                     "beat0": f["beat"][offsets] if len(f["beat"]) else np.zeros(len(runs)),
                     "end": (f["starts"][last] + f["waves"][last]) if len(f["starts"]) else np.zeros(len(runs)),
                     "moves": np.array([bool(r["offpitch"].any() or r["vibrato"].any()) for r in runs], bool),
                     "until": np.array([r["until"] for r in runs], float),
                     "tail": np.array(["tail_u" in r for r in runs], bool),
                     "vib_rate": np.array([r["vib_rate"] for r in runs], float),
                     "osc": np.array([r["osc"] for r in runs], np.int64)}

    def reverb_runs(self, hz, left, ppq, runs):
        """The Effects tab's reverb: after each note a chain of slides ends with, its tone goes on for the reverb's
        length (cut where a note of the same pitch starts, as the falls are), with everything as it was when the note
        ended; made quieter and scattered in made() ("tail_u" = 0..1 of the way through it). runs = one
        oscillator's."""
        r, tones = self.reverb, hz["tones"]
        soft = SOFT / self.lift("reverb")  # (a compressor after it may lift the tail's end: it goes on further)
        level = self.most("reverb_level", r["level"])  # (the MOD tab may turn it up: as far as it can go)
        if level ** 2 <= soft:
            return []
        quiet = 1.0 - (soft / level ** 2) ** (1 / 3)  # (0..1 of the way: from here on too soft, left out)
        rack = [name for name in self.moved if name in MOD_RACK]  # (the Effects tab's: on over the tail, in time)
        leaving = {a["id"] for a, _, _ in links(tones)} | {a["id"] for a, _ in legato_links(hz.get("voice"), tones)}
        last, starts = {}, {}
        for run in runs:  # (each tone's held stretch that goes on longest)
            n = run["tone"]
            if run["held"] and len(run["starts"]) and (n["id"] not in last
                                                      or run["starts"][-1] > last[n["id"]]["starts"][-1]):
                last[n["id"]] = run
        for m in tones:
            starts.setdefault(pitch(m), []).append(m["t"])
        for s in starts.values():
            s.sort()
        out = []
        for n in tones:
            src = last.get(n["id"])
            if n["id"] in leaving or src is None:
                continue
            end = n["t"] + n["len"]
            same = starts[pitch(n)]
            k = bisect.bisect_left(same, end - 1e-9)
            nxt = same[k] if k < len(same) else math.inf
            full = r["length"]
            if "reverb_length" in self.linked:  # (Length moved: as it is when the note ends, by the newest note)
                full = float(setting_at(self.hz, "reverb_length", full, np.array([end]), None, {})[0])
            length = min(full, nxt - end)
            if length <= 1e-9:
                continue
            i = max(0, int(np.searchsorted(src["beat"], end, "right")) - 1)
            gate, at = float(src["waves"][i]), (left + end) * ppq
            count = int(math.ceil(quiet * length * ppq / gate))
            run = {k: (np.full(count, v[i]) if isinstance(v, np.ndarray) else v) for k, v in src.items()}
            run["starts"] = at + gate * np.arange(count)
            run["waves"], run["number"] = np.full(count, gate), np.arange(count)
            run["beat"] = run["starts"] / ppq - left
            run["since"] = src["since"][i] + (run["starts"] - src["starts"][i]) / ppq
            run["limits"] = np.full(count, math.floor(at + length * ppq + 0.5), np.int64)
            run["until"], run["held"] = np.inf, False
            run["tail_u"] = np.arange(count) * gate / (length * ppq)
            for k in self.turned:  # (the speeds as at the end, their waves going on from there)
                run[k] = (np.zeros(count) if k.endswith("fm_ratio_turns")
                          else src[k][i] + src[k[:-6]][i] * (run["beat"] - src["beat"][i]))
            mono = {}
            for name in rack:
                run[name] = setting_at(self.hz, name, setting_base(self.hz, name), run["beat"], None, mono)
            out.append(run)
        return out

    def added_up(self, name):
        """An Effects tab Rate the MOD tab moves (the whole sound's: sources from the newest note): (grid beats from
        the shape's start, its waves so far there), added up step by step so a Rate moving never jumps. The
        Flanger's from the song's start (as the plain one counts: a Split leaves it), more coarsely before the shape."""
        beats = self.flat["beat"]
        end = float(beats.max()) if len(beats) else 0.0

        def summed(a, b):
            dt = max(TIME_STEP, (b - a) / 100000)
            g = a + np.arange(int((b - a) / dt) + 2) * dt
            rate = setting_at(self.hz, name, setting_base(self.hz, name), g, None, {})
            return g, np.concatenate([[0.0], np.cumsum(rate[:-1] * dt)])
        g, turns = summed(0.0, end)
        if name == "flanger_rate" and self.left > 0:
            before = summed(-self.left, 0.0)
            turns = turns + float(np.interp(0.0, *before))
        return g, turns

    def rack_wave(self, name, plain, beat):
        """How many waves of an Effects tab Rate (plain: as set) have gone by at beat (from the shape's start)."""
        if name in self.rack_turns:
            return np.interp(beat, *self.rack_turns[name])
        return plain * beat

    def most(self, name, plain):
        """The highest a knob that makes no line can be (plain: as set): as far as the MOD tab's links can take it."""
        return setting_most(self.hz, name, plain) if name in self.moved else plain

    def made(self, key):
        """A key's repeats, (start, end) ticks in order, none overlapping, and each one's part of the velocity.
        Every run x Voice copy is a "part" (in that order); all parts are worked out together, in one set of arrays
        (pack)."""
        got = self.got.get(key)
        if got is not None:
            return got
        x = min(1.0, max(0.0, (key - self.lo) / self.n))  # where the key is among the shape's keys, 0 = the lowest
        xv = min(1.0, max(0.0, (key - self.lo) / max(1, self.n - 1)))  # (for loudness: 1 = the highest key)
        noise = np.random.default_rng(1000 + key)  # (the same every time: the key is the seed)
        row = key - self.lo
        oscs = [0, 1] if self.osc2 else [0]  # (the oscillators this key row plays: OSC B's Split keys = turns)
        if self.a_off:
            oscs = [1]
        elif self.osc2 and self.osc2.get("split") and self.n > 1:  # (one key: plays both)
            oscs, row = [row % 2], row // 2  # (the Voice copies' turns: by pairs of rows then)
        which = list(range(len(self.copies))) if self.same else [row % len(self.copies)]  # (Voice copies)
        cents = [self.copies[i] for i in which]
        if not self.same:  # (the chorus and flanger take turns among the keys of the same copy (and oscillator),
            row //= len(self.copies)  # so every copy has some moved)
        chorus = self.chorus if row % 2 == 1 else None  # (every other key)
        f, each = self.flat, self.each
        runs = np.flatnonzero(np.isin(each["osc"], oscs)) if self.osc2 else np.arange(len(self.runs))
        run = np.repeat(runs, len(cents))  # each part's run
        copy = np.tile(np.array(which, np.int64), len(runs))  # ... and copy
        scale = np.tile(np.array([2.0 ** (-c / 1200.0) for c in cents]), len(runs))
        n = each["n"][run]
        # A stretch of tone: off pitch and vibrato make its waves longer or shorter one after the other (every value
        # of a repeat taken for the one with the same number; past the end, the last one's), so it may take more or
        # fewer repeats to fill the stretch; scale = every wave that many times as long (a Voice copy's own tone, or
        # the chorus: none past the stretch's end)
        first = np.concatenate([[0], np.cumsum(n)[:-1]]).astype(np.int64)  # (each part's first repeat as it was)
        part = np.repeat(np.arange(len(run)), n)
        src = each["offsets"][run][part] + np.arange(len(part)) - first[part]
        wide = np.repeat(scale, n)
        if "detune" in self.moved:  # (Detune moved: each repeat's own spread between the copies)
            spread = np.tile(np.array([i / (len(self.copies) - 1) - 0.5 for i in which]), len(runs))
            wide = 2.0 ** (-f["detune"][src] * np.repeat(spread, n) / 1200.0)
        if chorus:  # (up to depth cents and back, counted from the shape's start: it runs on over the notes)
            depth = f["chorus_depth"][src] if "chorus_depth" in self.moved else chorus["depth"]
            wide = wide * 2.0 ** (-depth * (1.0 - np.cos(2.0 * np.pi * self.rack_wave("chorus_rate", chorus["rate"],
                                                                                        f["beat"][src])))
                                  / 2.0 / 1200.0)
        scaled = np.bincount(part, wide != 1.0, len(run)) > 0
        moves = (each["moves"][run] | scaled) & (n > 0)
        vib = (f["vibrato_rate_turns"][src] if "vibrato_rate" in self.moved  # (its Rate moved: added up)
               else each["vib_rate"][run][part] * (f["beat"][src] - each["beat0"][run][part]))
        stretch = wide * (1.0 + OFF_PITCH * f["offpitch"][src] * (x - 0.5)) * (
            1.0 + VIBRATO * f["vibrato"][src] * np.sin(2.0 * np.pi * vib))
        size = np.where(moves, n + n // 10 + 3, n)
        start = np.concatenate([[0], np.cumsum(size)[:-1]]).astype(np.int64)
        part = np.repeat(np.arange(len(run)), size)
        number = np.arange(len(part)) - start[part]
        at = np.minimum(number, n[part] - 1)
        src = each["offsets"][run][part] + at
        moved = moves[part]
        waves = np.where(moved, f["waves"][src] * stretch[np.minimum(first[part] + at, len(stretch) - 1)],
                         f["waves"][src]) if len(part) else np.zeros(0)
        starts = f["starts"][src].copy()
        for size_k in np.unique(size[moves]):  # (summed wave by wave, as each part on its own would be)
            rows = np.flatnonzero(moves & (size == size_k))
            idx = start[rows][:, None] + np.arange(size_k)
            sums = np.cumsum(waves[idx], axis=1)
            starts[idx[:, 1:]] = starts[idx[:, :1]] + sums[:, :-1]
        keep = ~(moved & scaled[part]) | (starts < each["end"][run][part] - 1e-6)
        part, number, src, waves, starts = part[keep], number[keep], src[keep], waves[keep], starts[keep]
        late = f["slant"][src] * x + np.floor(x * f["groups"][src]) / f["groups"][src]
        if self.starting is not None:  # (Random start: each copy's waves start that far in, the same all through)
            amount = f["random"][src] if "random" in self.moved else self.random
            late = late + amount * self.starting[run, copy][part]
        # the random numbers each part takes, in order: its Noisy ones (if it has any), then the reverb's
        count = np.bincount(part, minlength=len(run))
        noisy = np.bincount(part, f["noisy"][src] != 0, len(run)) > 0
        tail = each["tail"][run]
        took = count * noisy + count * tail
        base = np.concatenate([[0], np.cumsum(took)[:-1]]).astype(np.int64)
        drawn = noise.random(int(took.sum()))
        place = base[part] + np.arange(len(part)) - np.concatenate([[0], np.cumsum(count)[:-1]]).astype(np.int64)[part]
        if noisy.any():
            on = noisy[part]
            late = np.where(on, late + f["noisy"][src] * drawn[np.where(on, place, 0)], late)
        osc = each["osc"][run][part]  # (each repeat's oscillator: its own Mode)
        grid = np.zeros(len(late))  # (Bitcrush's grid in ticks, 0 = none)
        for o in range(len(self.modes)):
            mine = osc == o
            mode = self.mode_now(o, src)
            if mode.get("kind") == "growl" and mine.any():  # (late by turns: 0 .. amount over `every` waves)
                every = int(mode["every"])
                late = np.where(mine, late + GROWL * mode["amount"] * (number % every) / (every - 1), late)
            if mode.get("kind") == "crush" and np.any(mode["amount"] > 0):
                grid = np.where(mine, mode["amount"] ** 2 * CRUSH * self.ppq, grid)
        # (the Effects tab's lateness: added after Bitcrush when it's on, as a synth's FX come after its oscillator)
        crushed = grid > 0
        fx_late = np.zeros(len(late))
        fl = self.flanger
        i = row  # (as the chorus: among the keys of the same copy and oscillator)
        mix = f["flanger_mix"][src] if "flanger_mix" in self.moved else fl["mix"] if fl else 0.0
        on = np.floor((i + 1) * mix + 1e-9) > np.floor(i * mix + 1e-9)  # (Mix: this key moves (each repeat's own))
        if fl and np.any(on):
            # (late by up to Depth and back, counted from the song's start like Random start: a Split keeps it)
            depth = f["flanger_depth"][src] if "flanger_depth" in self.moved else fl["depth"]
            if "flanger_rate" in self.rack_turns:  # (moved: added up from the song's start)
                turns = self.rack_wave("flanger_rate", fl["rate"], f["beat"][src])
            else:
                turns = fl["rate"] * (self.left + f["beat"][src])
            moved = depth * (1.0 - np.cos(2.0 * np.pi * turns)) / 2.0
            if np.ndim(on):
                moved = np.where(on, moved, 0.0)
            fx_late = np.where(crushed, fx_late + moved, fx_late)
            late = np.where(crushed, late, late + moved)
        tailed = tail[part]
        tail_u = f["tail_u"][src]  # (the reverb's: more and more scattered)
        if tailed.any():
            later = place + (count * noisy)[part]
            spread = f["reverb_scatter"][src] if "reverb_scatter" in self.moved else self.reverb["scatter"]
            scatter = spread * 0.5 * tail_u * drawn[np.where(tailed, later, 0)]
            fx_late = np.where(tailed & crushed, fx_late + scatter, fx_late)
            late = np.where(tailed & ~crushed, late + scatter, late)
        starts = starts + late * waves
        fx_late = fx_late * waves  # (ticks)
        hear = f["beat"][src]  # (the beat the compressor's turn-down is read at)
        if self.comp and tailed.any() and not self.after("reverb"):  # (a tail after it: as at its note's end)
            hear = np.where(tailed, each["beat0"][run][part], hear)
        limits = f["limits"][src]
        until = each["until"][run][part]
        fade = f["echo_fade"][src] if "echo_fade" in self.moved else None  # (the echo's, each repeat's own)
        gap = f["echo_time"][src] if "echo_time" in self.moved else None
        quiet = np.zeros(len(starts), bool)
        # (each repeat's note's own velocity, of 127 (0 = the Hz bass's), and its layer's loudness line: on the
        # velocity at the end, after the effects, as a mixer's fader)
        per = np.column_stack([f["own"][src], f["layer"][src]]) if len(src) else np.zeros((0, 2))
        if self.loud:
            trem = self.trem(src)
            where = f["sweep"][src]
            if self.track or "sweep_track" in self.moved:  # (Key track: one key row for every key the note is above
                track = f["sweep_track"][src] if "sweep_track" in self.moved else self.track  # the Hz bass's tone)
                where = where + track * f["track"][src] / max(1, self.n - 1)
            swept = 0.08 + 0.92 * np.clip(np.cos(np.pi * (xv - where)), 0.0, 1.0) ** 4
            loud = np.where(f["swept"][src], swept, 1.0)
            loud = loud * (1.0 + np.cos(2.0 * np.pi * WAH * f["wah"][src] * (xv - 0.5))) / 2.0
            if any(self.trem_in) or "tremolo_in" in self.timed:  # (coming in after Delay over Rise, from each
                come = self.trem_come(src)  # note's start)
                d = trem[1] * come
                loud = loud * (1.0 - d * (1.0 - np.cos(2.0 * np.pi * f["turns"][src])) / 2.0)
            else:
                loud = loud * (trem[0] + trem[1] * (1.0 + np.cos(2.0 * np.pi * f["turns"][src])) / 2.0)
            if "blend" in self.moved:  # (Blend moved by the MOD tab: each repeat's own, as blend_gains)
                b, middle = f["blend"][src], self.middle[copy][part]
                gains = np.where(middle, np.minimum(1.0, 2.0 * (1.0 - b)), np.minimum(1.0, 2.0 * b))
            else:
                gains = self.gains[copy][part]
            vol = np.where(f["has_volume"][src], f["volume"][src], 1.0) * (
                gains * f["level"][src])  # (Blend; OSC B's Level)
            loud = loud * vol
            if tailed.any():  # (the reverb: starting at its level, fading)
                level = f["reverb_level"][src] if "reverb_level" in self.moved else self.reverb["level"]
                loud = np.where(tailed, loud * level ** 2 * (1.0 - tail_u) ** 3, loud)
            soft = np.where(number % 2 == 1, 1.0 - f["octave"][src], 1.0)  # (on the velocity itself)
            if self.shaped:  # SUB hits in every wave, each as loud as the waveforms say there (wave_hits)
                plain = ~np.any([f["has_" + name][src] for name in WAVES], axis=0)
                got = [(np.zeros(0, np.int64), np.zeros(0), np.zeros(0))]
                for o, mode in enumerate(self.modes):  # (each oscillator with its own Mode)
                    sel = np.flatnonzero(osc == o) if self.osc2 else np.arange(len(starts))
                    if not len(sel):
                        continue
                    where, mix = wave_hits([(WAVES[name], f[name][src[sel]], f["has_" + name][src[sel]])
                                            for name in WAVES], plain[sel], number[sel], f["since"][src[sel]],
                                           self.mode_now(o, src[sel]))
                    keep = mix >= SOFT
                    got.append((sel[np.nonzero(keep)[0]], (starts[sel, None] + where * waves[sel, None])[keep],
                                mix[keep]))
                    if not self.osc2:
                        break
                rows = np.concatenate([g[0] for g in got]).astype(np.int64)
                starts = np.concatenate([g[1] for g in got])
                limits, until, hear, fx_late = limits[rows], until[rows], hear[rows], fx_late[rows]
                grid, crushed, osc = grid[rows], crushed[rows], osc[rows]
                fade = fade[rows] if fade is not None else None
                gap = gap[rows] if gap is not None else None
                env, mix, soft = loud[rows], np.concatenate([g[2] for g in got]), soft[rows]
                quiet = vol[rows] < SOFT
                per = per[rows]
            else:
                env, mix = loud, np.ones(len(loud))
                quiet = vol < SOFT
        else:
            env, mix, soft = np.ones(len(starts)), np.ones(len(starts)), np.ones(len(starts))
        # (each note's velocity part = sqrt(env x mix) x soft x the echo's gain: env = its loudness, which the
        # compressor turns up and down; mix and soft = the waveform's and Octave below's part, which it leaves alone)
        if crushed.any():  # (onto a coarse grid of ticks; then the Effects tab's lateness)
            grid = np.where(crushed, grid, 1.0)
            starts = np.where(crushed, np.floor(starts / grid + 0.5) * grid + fx_late, starts)
        keep = starts < until - 1e-6
        late_comp = self.echo is not None and self.after("echo")  # (the compressor squeezing the echoes too)
        if self.comp:  # (every key row turned up / down alike: the tone across the keys stays)
            pre = env[keep]
            env[keep] = np.minimum(1.0, pre * self.squeezed(hear[keep]))
        all_starts, all_limits, all_env, all_quiet = [starts[keep]], [limits[keep]], [env[keep]], [quiet[keep]]
        all_gain = [np.ones(int(keep.sum()))]
        if self.echo and len(run):  # (the whole sound again, later and quieter: one more line of notes each)
            made = all_starts[0], all_limits[0], all_env[0], all_quiet[0]
            most = self.lift("echo")  # (the compressor after it may bring a quiet one up)
            fades = self.echo["fade"] if fade is None else fade[keep]
            times = self.echo["time"] if gap is None else gap[keep]  # (Time moved: each repeat's own)
            for i in range(1, int(self.echo["repeats"]) + 1):
                gain = fades ** (i / 2.0)  # (loudness goes with the velocity squared)
                if np.max(gain, initial=0.0) ** 2 * most < SOFT:
                    break
                shift = i * times * self.ppq
                all_starts.append(made[0] + shift)
                all_limits.append(made[1] + (int(math.floor(shift + 0.5)) if gap is None
                                             else np.floor(shift + 0.5).astype(np.int64)))
                if late_comp:  # (squeezed as heard then, among the other echoes)
                    all_env.append(np.minimum(1.0, pre * gain * gain
                                              * self.squeezed(hear[keep] + i * times)))
                else:
                    all_env.append(made[2])
                all_gain.append(np.full(len(made[0]), 1.0 if late_comp else gain))
                all_quiet.append(made[3])
        if len(run):
            st, li = np.concatenate(all_starts), np.concatenate(all_limits)
            env, qu = np.concatenate(all_env), np.concatenate(all_quiet)
            sets = len(all_starts)  # (the sound and its echoes)
            mixed = env * np.tile(mix[keep], sets)
            fa = np.sqrt(mixed) * np.tile(soft[keep], sets) * np.concatenate(all_gain)
            if self.comp:  # (too quiet even after the compressor: left out, decided after it)
                qu = qu | (mixed * np.concatenate(all_gain) ** 2 < SOFT)
            pe = np.tile(per[keep], (sets, 1))
            if self.hz.get("loud"):  # (the layer's line where each note really starts: echoes and the reverb's
                pe[:, 1] = line_at(self.hz["loud"], st / self.ppq - self.left)  # tail too, like a fader)
            fa = fa * np.where(pe[:, 0] > 0, pe[:, 0], 1.0) * pe[:, 1]
            qu = qu | (pe[:, 1] <= 0)  # (the layer's line at 0: silence)
            if len(oscs) > 1 and qu.any():  # (both oscillators: one too quiet while the other sounds never cuts it)
                drop = silent_beside(st, li, qu, np.tile(osc[keep], sets))
                st, li, fa, qu, pe = st[~drop], li[~drop], fa[~drop], qu[~drop], pe[~drop]
            # (Blend, OSC B: repeats on one tick = the loudest one, left out only if all are)
            if (self.gains != 1.0).any() or "blend" in self.moved or self.osc2:
                first = np.lexsort((-fa, qu))
                st, li, fa, qu, pe = st[first], li[first], fa[first], qu[first], pe[first]
            sq, which = _grid(st, li)
            factor, mine = fa[which], pe[which, 0] > 0
            heard = ~qu[which]  # (volume 0: left out once every repeat has its end)
            sq, factor, mine = sq[heard], factor[heard], mine[heard]
        else:
            sq, factor, mine = np.zeros((0, 2), np.int64), np.zeros(0), np.zeros(0, bool)
        factor.setflags(write=False)
        mine.setflags(write=False)
        self.mine[key] = mine  # (which take their note's own velocity: factor)
        got = self.got[key] = (sq, factor)
        return got

    def trem(self, src=slice(None)):
        """The tremolo's (steady part, depth) for these repeats (src: their places in self.flat): the run's, or each
        repeat's own while the MOD tab moves its Depth."""
        if "tremolo_depth" not in self.moved:
            return self.runs[0]["trem"]
        d = self.flat["tremolo_depth"][src]
        return 1.0 - d, d

    def trem_come(self, src=slice(None)):
        """How much of the tremolo has come in (0..1) for these repeats: none for its Delay, then over its Rise, from
        each note's start (moved by the MOD tab: as timed_line works it out)."""
        if "tremolo_in" in self.timed:
            return self.flat["tremolo_in"][src]
        wait, rise = self.trem_in
        since = self.flat["trem_since"][src]
        return np.clip((since - wait) / rise, 0.0, 1.0) if rise > 0 else (since >= wait - 1e-9).astype(float)

    def mode_now(self, o, src):
        """Oscillator o's Mode (OSC A = 0, OSC B = 1) for these repeats: as set, or its amount knob each repeat's
        own while the MOD tab moves it (MODE_AMOUNTS); its Time moved: "progress" = how far through it each is."""
        mode = self.modes[o]
        prefix = "osc2_" if o else ""
        name = prefix + f"{mode.get('kind')}_{MODE_AMOUNTS.get(mode.get('kind'))}"
        if name in self.moved:
            mode = dict(mode, **{MODE_AMOUNTS[mode["kind"]]: self.flat[name][src]})
        if f"{prefix}{mode.get('kind')}_in" in self.timed:
            mode = dict(mode, progress=self.flat[f"{prefix}{mode['kind']}_in"][src])
        speed = {"fm": "ratio", "pulse": "rate"}.get(mode.get("kind"))
        if speed and f"{prefix}{mode['kind']}_{speed}" in self.moved:  # (Ratio / Rate moved: added up, wave_hits)
            name = f"{prefix}{mode['kind']}_{speed}"
            mode = dict(mode, **{speed: self.flat[name][src], "turns": self.flat[name + "_turns"][src]})
        return mode

    def after(self, kind):
        """True when the compressor is on and comes after the Effects tab's `kind` (so it squeezes what that makes)."""
        return bool(self.comp and kind in self.order and self.order.index(kind) < self.order.index("compressor"))

    def lift(self, kind):
        """How many times louder the compressor after `kind` can make something at most (its Gain), else 1: what's
        too quiet to keep is decided after it."""
        return 10.0 ** (self.most("compressor_gain", self.comp["gain"]) / 20.0) if self.after(kind) else 1.0

    def comp_curve(self):
        """The compressor as a real one works: it listens to the whole sound (every note sounding together, how loud
        each is from its Volume line and tremolo, not from which key row it's on: Sweep, Wah and Blend are its tone),
        turns it down by how far it's over the threshold, reaching that over Attack when it gets louder and letting
        go over Release when it gets quieter, then lifts it by Gain. (start beat, step, how many times as loud at
        each step); the sound's tail and echoes count only when it comes after them."""
        f, each, comp = self.flat, self.each, self.comp
        if not len(f["beat"]):
            return None
        lv = np.where(f["has_volume"], f["volume"], 1.0)
        if self.osc2:  # (OSC B as loud as its Level)
            lv = lv * f["level"]
        trem = self.trem()
        if any(self.trem_in) or "tremolo_in" in self.timed:
            lv = lv * (1.0 - trem[1] * self.trem_come() * (1.0 - np.cos(2.0 * np.pi * f["turns"])) / 2.0)
        else:
            lv = lv * (trem[0] + trem[1] * (1.0 + np.cos(2.0 * np.pi * f["turns"])) / 2.0)
        tails = np.repeat(each["tail"], each["n"])
        if tails.any():
            level = f["reverb_level"] if "reverb_level" in self.moved else self.reverb["level"]
            lv = np.where(tails, lv * level ** 2 * (1.0 - f["tail_u"]) ** 3, lv)
        ends = each["end"] / self.ppq - self.left
        t0 = float(each["beat0"].min())
        t1 = float(ends.max())
        echo = self.echo if self.after("echo") else None
        if echo:
            t1 += echo["repeats"] * self.most("echo_time", echo["time"])
        dt = max(1 / 128, (t1 - t0) / 100000)  # (a step: fine enough for the tremolo, never too many)
        size = int((t1 - t0) / dt) + 2
        power = np.zeros(size)
        faded = echo and "echo_fade" in self.moved  # (the echo's Fade moved: each step's, by the notes sounding)
        fades = np.zeros(size) if faded else None
        for r in range(len(self.runs)):  # (each note's loudness at every step it sounds; notes together add up)
            o, n = int(each["offsets"][r]), int(each["n"][r])
            if not n or each["tail"][r] and not self.after("reverb"):
                continue
            b, v = f["beat"][o:o + n], lv[o:o + n]
            k0, k1 = int(math.ceil((b[0] - t0) / dt)), int(math.floor((ends[r] - t0) / dt))
            if k1 >= k0:
                p = np.interp(t0 + np.arange(k0, k1 + 1) * dt, b, v) ** 2
                power[k0:k1 + 1] += p
                if faded:
                    fades[k0:k1 + 1] += p * np.interp(t0 + np.arange(k0, k1 + 1) * dt, b, f["echo_fade"][o:o + n])
        if echo:  # (the echoes after it count too)
            dry = power.copy()
            fade = fades / np.maximum(power, 1e-24) if faded else echo["fade"]
            gaps = None
            if "echo_time" in self.moved:  # (Time moved: each step's sound comes back as far apart as it says then)
                gaps = setting_at(self.hz, "echo_time", echo["time"], t0 + np.arange(size) * dt, None)
            for i in range(1, int(echo["repeats"]) + 1):
                if gaps is not None:
                    k = np.arange(size) + np.round(i * gaps / dt).astype(np.int64)
                    ok = k < size
                    np.add.at(power, k[ok], ((fade[ok] if faded else fade) ** (2 * i)) * dry[ok])
                    continue
                k = int(round(i * echo["time"] / dt))
                if k < size:
                    power[k:] += (fade[:size - k] if faded else fade) ** (2 * i) * dry[:size - k]
        # (the knobs as the MOD tab moves them, at each step: by the newest note, as a synth's effects)
        at = t0 + np.arange(size) * dt
        thr, ratio, gain = (setting_at(self.hz, f"compressor_{k}", comp[k], at, None) if f"compressor_{k}"
                            in self.moved else comp[k] for k in ("threshold", "ratio", "gain"))
        db = 10.0 * np.log10(np.maximum(power, 1e-24))
        want = np.maximum(0.0, db - thr) * (1.0 - 1.0 / ratio)  # (dB to turn down)
        down = np.empty(size)
        a = 1.0 - math.exp(-dt / comp["attack"]) if comp["attack"] > 0 else 1.0
        rel = 1.0 - math.exp(-dt / comp["release"]) if comp["release"] > 0 else 1.0
        now = 0.0
        if {"compressor_attack", "compressor_release"} & self.linked:  # (moved: each step's own)
            a, rel = (np.ones(size) * v if f"compressor_{k}" not in self.linked else np.where(
                t > 0, 1.0 - np.exp(-dt / np.maximum(t, 1e-12)), 1.0) for k, v, t in (
                ("attack", a, setting_at(self.hz, "compressor_attack", comp["attack"], at, None)),
                ("release", rel, setting_at(self.hz, "compressor_release", comp["release"], at, None))))
            for k, (w, up, back) in enumerate(zip(want.tolist(), a.tolist(), rel.tolist())):
                now += (w - now) * (up if w > now else back)
                down[k] = now
        else:
            for k, w in enumerate(want.tolist()):
                now += (w - now) * (a if w > now else rel)
                down[k] = now
        return t0, dt, 10.0 ** ((gain - down) / 20.0)

    def squeezed(self, beat):
        """How many times as loud the compressor makes the sound at these beats (from the left edge)."""
        if self.squeeze is None:
            return np.full(len(beat), 10.0 ** (self.comp["gain"] / 20.0))
        t0, dt, g = self.squeeze
        return g[np.clip(np.round((np.asarray(beat) - t0) / dt).astype(np.int64), 0, len(g) - 1)]

    def squares(self, key):
        """A key's repeats: (start, end) ticks in order, none overlapping."""
        return self.made(key)[0]

    def factor(self, key, starts):
        """What the effects make of the velocity of a key's notes (start ticks): 0..1 for each to multiply it by."""
        sq, factor = self.made(key)
        if not len(sq):
            return np.ones(len(starts))
        at = np.minimum(np.searchsorted(sq[:, 0], starts), len(sq) - 1)
        return np.where(sq[at, 0] == starts, factor[at], 1.0)

    def own(self, key, starts):
        """Which of a key's notes (start ticks) have their note's own velocity from its loudness line: their factor
        is then of 127, not of the shape's velocity."""
        sq, _ = self.made(key)
        if not len(sq):
            return np.zeros(len(starts), bool)
        at = np.minimum(np.searchsorted(sq[:, 0], starts), len(sq) - 1)
        return (sq[at, 0] == starts) & self.mine[key][at]
