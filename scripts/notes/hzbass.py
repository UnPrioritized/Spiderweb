"""Hz bass: spam so fast that the repeats sound like a tone.

A custom shape with sh["hz"] (and a spam fill) gets its spam gates from a tone instead of the gate box:
  {"key": the key whose tone is wanted, "cents": pitch adjustment (100 = one key), "bpm": the BPM the gates are
   worked out for, "fixed": True = every gate rounded to a whole tick instead (the tone is a little off; the
   higher PPQ x BPM, the less), "auto": cents (0..AUTO_MOST) = instead of "fixed": a tone held still gets fixed
   gates when those are at most this far off its exact tone, else mixed ones; a slide is always mixed. A placed
   tone may have its own "auto" (see threshold)}
sh["gate"] is then one wave of that tone, gate = BPM / (60 x Hz) beats, NOT rounded to a whole tick: the notes sit
on one grid counted from tick 0 (every key in step), note n starting at round(n x gate), so gates of two sizes are
mixed and the tone comes out exact (custom.chop_even). The tone depends on the BPM, so a changed BPM leaves it off
until it's updated (the panel warns); a changed PPQ keeps the tone.

Placed notes (the Hz bass window): hz["tones"] = [{"t": start in beats from the shape's left edge, "len": beats,
"key": its tone, "cents": its own tune, "auto": its own Auto gates threshold (optional), "gate": "fixed" / "mixed" =
its own gates while held, whatever the Hz bass's (optional, see own_gate), "id": its number (never reused in the shape), "to": its slides,
"vel": its own loudness line (optional: [[u 0..1 along the note, velocity 1..127], ...]; its repeats take that
velocity instead of the shape's, the effects still on it: velocity_parts)}, ...].
hz["loud"] = the layer's loudness line (optional: [[beat, 0..1], ...] like an effect line): every repeat's velocity
times it, after everything else, like a mixer's fader (0 = left out).
Each tone makes repeats one wave apart for as long as it lasts; tones sounding together are a chord. A slide is
made by the user and belongs to two tones: "to" = [{"id": the tone slid to, "out": lead out, "in": lead in
(beats)}, ...]: the tone glides from `out` before this tone's end to `in` after the start of the other one (which
must start at or after this one's end), the wave getting shorter or longer on the way. A tone can slide to several
tones, and several can slide to one: each slide is a line of its own, next to the tone held (tone_runs). Without a
slide the tone just stops at its end. The red line in the window is what is really heard (heard).
All the repeats together, each lasting until the next one starts, are the squares every key of the shape is chopped
by (custom.chop_grid), so a chord takes one channel. Nothing sounds where no tone is.
LAYERS (like a DAW's tracks): hz["layers"] (clean_layers) = more sets of notes in one Hz bass, each with its own
sound; the picked one's notes and sound are where a Hz bass without layers has them, so all editing works on it.
Each layer heard (heard_layers: mute / solo) makes its notes as a Hz bass of its own, put together by
engine._notes_tracks.
hz["grow"] = the shape is kept as long as its tones (fit_length). hz["own"] = made with the Hz bass tool: a box
that is nothing but its tones (it goes when its last tone is deleted; the panel shows the keys it repeats).
hz["length"] = the Note length (clean_length: % or beats, shown in ticks; missing = 100 %), a layer's own in hz["own_length"] (one of EXTRAS, so
Copy sound and presets carry it); hz["stick"] = the last note of each run keeps a whole wave, even past the shape.
Both only change the notes' ends once they're made (note_lengths), never which repeats there are.

Effects: hz["fx"] = {effect: [[beat, value], ...]}: one line through points over all the tones (beat counted like a
tone's "t", from the shape's left edge; flat before the first point and after the last), value 0..1.
hz["loop"] = {effect: beats}: that effect's points are one repeat of so many beats (from 0 to it), repeated from the
shape's left edge on over and over (line_at; like an automation that repeats every beat). hz["amount"] = {effect:
[[beat, value(, bend)], ...]}: for a repeating effect, a line over the notes (beats like "fx") saying how strong the
repeat is: 1 = as drawn, 0 = as if the effect were off (NEUTRAL). hz["from"] = {effect: "note" / "restart"}: a
repeating effect counted from each note's start instead of the shape's left edge (like an envelope): "note" = its
one repeat plays once from each note's start, then stays on its last value; "restart" = it repeats, starting over
at each note. Each note has its own, like the voices of a synth: counted from its own start (a note starting
while others sound starts over alone; KeyGrid works it out for each tone's repeats). A
note reached by a slide never starts it over (note_beats). hz["fit"] = [effects]: such an effect's repeat is
stretched over each note (and the notes it slides on to) instead of lasting its own beats. hz["sustain"] = {effect:
beat in its repeat}: for one played once per note (not stretched), like a synth's envelope: the line plays up to
that beat, stays there while the note (its chain of slides) lasts, and the rest of it (the fall) plays after the
note ends (a note ending before the sustain point falls from where it got to; sustained). The sound of the tones a
chain ends with then goes on that long after them, the longest fall of all, under the notes after it (both sound
there, like a chord), cut only where a note of the same tone starts (tails). hz["off"] = [effects]
switched off (Bypass): their lines are kept but do nothing (live). They change the colour of the tone, not its pitch, by making the keys hit at different spots of the wave
(how late a key is, in waves, is added up over the effects; a key starts that late, and a repeat pushed past its
own tone's end is left out):
  "slant": every key starts its repeats a bit later than the key below it; the value is how much of one wave the
  shape's keys are spread over (0 = all together).
  "groups": the keys take turns in groups, evenly spread over the wave: 0 = all together, then 2, 3... up to
  GROUPS groups at 1 (group_count).
  "offpitch": every key repeats a little faster or slower than the tone, the lowest key the fastest, the highest
  the slowest: the keys drift apart and meet again by themselves. 1 = OFF_PITCH of the tone between them. (Its
  waves are made that much longer or shorter, one after the other, so a key never skips or doubles a repeat.)
  "noisy": every repeat of every key is late by a random bit, up to the value of one wave.
  "vibrato": the tone itself goes up and down, VIBRATO_RATE times a beat (or hz["lfo"]["vibrato_rate"], set by the
  synth window's knobs), by value x VIBRATO of its pitch (every key the same; its waves made shorter and longer like
  off pitch's).
  "pitch": unlike the others it DOES change the pitch: the tone bent up or down by the line, 0.5 = as placed, 1 / 0 =
  PITCH keys up / down, or the Pitch box's Range (hz["lfo"]["bend_range"]; bent, in tone_runs, so the red line shows it and every key is in step).
With effects every key has its own repeats (KeyGrid, custom.chop_keys); without any, nothing changes.
Five more change how hard the keys hit instead (KeyGrid.factor -> velocity_factor, used by engine._notes_tracks on
top of the shape's own velocity; loudness goes with velocity squared):
  "volume": how loud, 1 = as the shape's velocity, 0 = silence (repeats softer than SOFT are left out, and the one
  before them still ends where it would have).
  "sweep": a bump of loudness over the keys; the value is where it is, 0 = the lowest key, 1 = the highest
  (hz["lfo"]["sweep_track"] moves it with each note's pitch: see LFO).
  "wah": loud and quiet stripes over the keys, more of them the higher the value (0 = every key full).
  "tremolo": every key louder and quieter in turn, value x TREMOLO times a beat (0 = steady), down to 1 -
  TREMOLO_DEPTH of its loudness (or 1 - hz["lfo"]["tremolo_depth"], set by the synth window's knobs), coming in
  from each note's start after hz["lfo"]["tremolo_wait"] beats over ["tremolo_rise"] (none: there at once).
  "octave": every other repeat softer, down to velocity 1 at 1: the tone an octave below comes in.
Waveforms ("sine", "square", "saw", "triangle"): every key hits SUB times in each wave instead of once, and how
hard each of those hits is follows the shape drawn over one wave (WAVES), which takes overtones out of the tone
(measured: sine leaves almost only the lowest, square and triangle take out every second one, saw tilts them).
The value goes from the plain tone (0: one hit a wave) to the whole shape (1); hits too soft to matter are left
out, so the note count grows with the value, up to SUB times as many.
hz["voice"] = the synth window's Voice box (not lines; clean_voice): {"voices": copies of the sound (2..VOICES),
"detune": cents between the lowest and the highest copy, their tones spread evenly between, "same": True = every key
plays every copy (as many times the notes) instead of each key one copy in turn (no extra notes), "glide": beats a
note takes to glide in from the tone of the note before it (glides), "curve": how (glide_left), "touching": True = only from a note that ends
where it starts, "legato": True = a note that starts right where another ends carries on its effects (no new attack,
no fall after the first: legato_links)}. Glide only bends the tone: each note still starts its effects over, like a
synth's voices (unless Legato joins them).
hz["mode"] = the Wave box's Mode (MODES, clean_mode): FM, Pulse width or Sync change the hits in each wave
(wave_hits), Growl and Bitcrush when each one lands. hz["osc2"] = the second oscillator, OSC B (OSC2, clean_osc2;
there only while it's on): every note played again as its own set of repeats, `octave` x 12 + `semi` keys + `fine`
cents from it, its own "wave" (one of WAVES at `shape`, or the plain tone) and "mode" (as hz["mode"]), `level` as
loud; "split": True = the key rows take turns playing OSC A or OSC B (no extra notes) instead of each row both
(twice the notes); "a_off": True = OSC A switched off (only OSC B sounds, on every row). With Fine, OSC B's gates
alternate (whole-tick ones would round a few cents away). Everything else (the lines, Voice, the Effects tab) works on both. A synth window box switched off (Bypass) puts its lines in
hz["off"] and moves its own setting (mode / voice) to hz["bypass"], which also names it (clean_bypass). Its knobs
that do nothing right now are kept in hz["kept"] (clean_kept), its macros in hz["macro"] (clean_macro).
hz["mod"] = the MOD tab (the matrix, clean_mod): two more LFOs, two more envelopes, each note's velocity and pitch,
linked to effect lines or knobs that make no line (MOD_SETTINGS), which they move over time while the notes are made
(fx_at, setting_at, mod_value).
hz["trace"] = the drawn wave shape (clean_trace): a line through one wave, the keys from bottom to top; each key row
hits wherever the line crosses it (trace_line: bent pieces as straight ones, then trace_hits: stretched to all the
keys, several crossings = several hits a wave),
before the lateness effects add theirs. Missing = straight up at the wave's start, every key together.

The code is in parts, each using only the ones before it: hz_settings (limits, starting values, checking saved
settings), hz_lines (effect lines over time), hz_glide (slides, glides, legato chains), hz_modulate (the MOD tab's
engine), hz_arp (the arpeggio), hz_runs (each tone's run of waves, gates, the red line), hz_grid (KeyGrid: every
key's own repeats with effects), then this file (a shape's squares and velocity, moving / fitting its box). Every
name of theirs can be taken from here."""

import functools
import json

import numpy as np

from notes.hz_settings import *  # noqa: F401,F403
from notes.hz_lines import *  # noqa: F401,F403
from notes.hz_lines import _shifted_line, _turned_repeat
from notes.hz_glide import *  # noqa: F401,F403
from notes.hz_modulate import *  # noqa: F401,F403
from notes.hz_arp import *  # noqa: F401,F403
from notes.hz_runs import *  # noqa: F401,F403
from notes.hz_runs import _grid, _heard, _limits, _spans, _squares, _whole
from notes.hz_grid import *  # noqa: F401,F403


@functools.lru_cache(maxsize=16)
def _key_grid(hz_json, left, ppq, lo, n):
    return KeyGrid(json.loads(hz_json), left, ppq, lo, n)


HZ_KINDS = ("custom", "funnel")  # the shapes that can be a Hz bass (a funnel: the whole of it, both sides of the wall)


def funnel_box(sh):
    """A funnel's (first beat, last beat, lowest key, highest key): around its lines, wall and curves."""
    from notes.funnel import funnel_strokes  # (funnel.py needs custom.py, which needs this file)
    pts = [p for s in funnel_strokes(sh) for p in s]
    bs, ps = [b for b, _ in pts], [p for _, p in pts]
    return min(bs), max(bs), min(ps), max(ps)


def key_range(sh):
    """The lowest and highest key of a custom shape's box (a funnel: around it)."""
    if sh["kind"] == "funnel":
        _, _, lo, hi = funnel_box(sh)
        return round(lo), round(hi)
    ps = [p for _, p in sh["pts"]]
    ps.append(ps[1] + ps[2] - ps[0])
    return round(min(ps)), round(max(ps))


def velocity_factor(sh, ppq, starts, keys):
    """What the effects make of the velocity of a shape's notes (arrays: start ticks, keys): a number from 0 to 1
    for each to multiply it by, or None when there's no effect that changes it."""
    got = velocity_parts(sh, ppq, starts, keys)
    return None if got is None else got[0]


def velocity_parts(sh, ppq, starts, keys):
    """velocity_factor's numbers, and True for each note whose own note has a loudness line (tone["vel"]): its number
    is then of 127, not of the shape's velocity. None when nothing changes the velocity."""
    if not has_fx(live(sh["hz"])) or not len(starts):
        return None
    grid = squares(sh, ppq)
    if not grid.loud:
        return None
    out, own = np.ones(len(starts)), np.zeros(len(starts), bool)
    for key in np.unique(keys):
        rows = np.flatnonzero(keys == key)
        out[rows], own[rows] = grid.factor(int(key), starts[rows]), grid.own(int(key), starts[rows])
    return out, own


def squares(sh, ppq):
    """The repeats of a shape with placed tones: an array of (start, end) ticks in order, none overlapping. When
    it has effects: a KeyGrid (each key has its own)."""
    hz = {k: v for k, v in live(sh["hz"], left_edge(sh)).items() if k not in LENGTH_KEYS}  # (made after: note_lengths)
    hz_json = json.dumps(hz, sort_keys=True)
    if has_fx(hz):
        lo, hi = key_range(sh)
        return _key_grid(hz_json, left_edge(sh), ppq, lo, hi - lo + 1)
    return _squares(hz_json, left_edge(sh), ppq)


LENGTH_KEYS = ("length", "own_length", "stick")


def note_lengths(hz, notes, ppq, stick=True):
    """A Hz bass layer's notes ((start, end, key) ticks, as made: each lasting until the next one on its key starts)
    with its Note length (a layer's own, else the Hz bass's): each lasts that share of how long it does now ("pct") or
    that many beats in whole ticks, at least 1 (follows a PPQ change), never longer than it is now ("beats").
    hz["stick"] (stick=False: a funnel, no notes past its wall): the last note of each run (cut where its tones end)
    is first made a whole wave, as long as the one before it (a lone one: as it is), even past the shape. Never past
    the next note on its key."""
    got = hz.get("own_length") or hz.get("length") or FULL_LENGTH
    stick = stick and hz.get("stick")
    if not len(notes) or (got == FULL_LENGTH and not stick):
        return notes
    o = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, k = notes[o, 0], notes[o, 1], notes[o, 2]
    big = np.int64(1) << 40
    keyed = k.astype(np.int64) * big + s
    j = np.searchsorted(keyed, keyed, "right")  # (the next note on its key, starting later)
    has = j < len(s)
    j = np.minimum(j, len(s) - 1)
    has &= k[j] == k
    room = np.where(has, s[j] - s, big)
    now = e - s
    last = ~(has & (s[j] <= e))  # (ends a run: no note on its key starts where it ends)
    full = now
    if stick:  # (the note before it on its key runs into it: a whole wave is about as long as that one)
        before = np.concatenate([[False], ~last[:-1] & (k[:-1] == k[1:])])
        full = np.where(last & before, np.minimum(np.maximum(now, np.concatenate([[0], now[:-1]])), room), now)
    if "beats" in got:
        new = np.minimum(length_ticks(got, ppq), full)
    else:
        new = np.floor(full * got["pct"] / 100.0 + 0.5)
    out = notes.copy()
    out[o, 1] = s + np.maximum(new, 1).astype(np.int64)
    return out


def left_edge(sh):
    """The beat a custom shape's box starts at (its tones count from there; a funnel: its first beat)."""
    if sh["kind"] == "funnel":
        return funnel_box(sh)[0]
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    return min(b0, b1, b2, b1 + b2 - b0)


def shifted_hz(hz, d):
    """hz for the shape's left edge moved d beats to the LEFT (d < 0: to the right), so every tone and effect point
    stays on the same beat in the song. Tones then before the edge are left out (one reaching past it starts at it);
    a repeating effect counted from the edge starts its repeat that much later, so it stays in step."""
    if abs(d) < 1e-12:
        return hz
    if hz.get("layers"):
        return each_layer(hz, lambda l: shifted_hz(l, d))
    hz = json.loads(json.dumps(hz))
    tones = []
    for n in hz.get("tones") or ():
        t, end = n["t"] + d, n["t"] + n["len"] + d
        if end <= MIN_LEN:
            continue
        if t < 0 and n.get("vel"):  # (cut at the edge: the rest of its loudness line)
            n["vel"] = vel_part(dict(n, t=n["t"] + d), 0.0, end)
        if t < 0 and n.get("bend"):  # (... and of its bend line; none left when that part is at 0 all along)
            n["bend"] = bend_part(dict(n, t=n["t"] + d), 0.0, end)
            if n["bend"] is None:
                del n["bend"]
        n["t"], n["len"] = max(0.0, t), max(MIN_LEN, end - max(0.0, t))
        tones.append(n)
    if "tones" in hz:
        ids = {n.get("id") for n in tones}
        for n in tones:
            n["to"] = [s for s in n.get("to") or () if s.get("id") in ids]
        hz["tones"] = tones
    loop, froms = hz.get("loop") or {}, hz.get("from") or {}
    for name, pts in (hz.get("fx") or {}).items():
        if name in froms:  # (counted from each note: the edge doesn't matter)
            continue
        hz["fx"][name] = _turned_repeat(pts, d, loop[name]) if name in loop else _shifted_line(pts, d)
    for name, pts in (hz.get("amount") or {}).items():
        hz["amount"][name] = _shifted_line(pts, d)
    if hz.get("loud"):
        hz["loud"] = _shifted_line(hz["loud"], d)
    if hz.get("mod"):  # (the Free LFOs: counted that much later, so they stay in step too)
        phase = hz["mod"].get("phase", 0.0) + d
        hz["mod"].pop("phase", None)
        if abs(phase) > 1e-12:
            hz["mod"]["phase"] = phase
    return hz


def hz_up_to(hz, width):
    """hz without the tones starting at or after width beats from the left edge (a part split off a Hz bass: they
    made nothing in it); slides to them go too."""
    if hz.get("layers"):
        return each_layer(hz, lambda l: hz_up_to(l, width))
    tones = [n for n in hz.get("tones") or () if n["t"] < width - 1e-9]
    if len(tones) == len(hz.get("tones") or ()):
        return hz
    ids = {n.get("id") for n in tones}
    for n in tones:
        n["to"] = [s for s in n.get("to") or () if s.get("id") in ids]
    return dict(hz, tones=tones)


def fit_length(sh):
    """hz["grow"]: the shape made as long as its tones and the falls after them (stretched from its left edge). Not a
    funnel: it keeps the length it's drawn."""
    if sh["kind"] == "funnel":
        return
    span = max(max(sound_span(l) for l in layers_of(sh["hz"])), MIN_LEN)  # (every layer's notes)
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    bs = (b0, b1, b2, b1 + b2 - b0)
    left, width = min(bs), max(bs) - min(bs)
    if width > 1e-12:
        for p in sh["pts"]:
            p[0] = left + (p[0] - left) * span / width


def shortest_gate(hz, ppq):
    """The shortest gate, in ticks, a shape's Hz bass uses (its highest tone, bent up by the "pitch" effect's
    highest point; with layers, the shortest of the layers heard)."""
    if hz.get("layers"):
        return min(shortest_gate(l, ppq) for l in heard_layers(hz) or layers_of(hz))
    played = live(hz)  # (the Arpeggio box's octaves go higher)
    # (each note bent up by its own bend line, as far as the Range lets it)
    top = max((pitch(n) + max(0.0, min(bend_range(played), max((p[1] for p in n.get("bend") or ()), default=0.0)))
               for n in played.get("tones") or ()), default=hz["key"])
    pts = (played.get("fx") or {}).get("pitch") if hz.get("tones") else None
    if pts:  # (the amount line only ever weakens it; the MOD tab's links push it up as far as they reach)
        push = sum(abs(link["amount"]) if link.get("bipolar") else max(0.0, link["amount"])
                   for link in (played.get("mod") or {}).get("links", ()) if link["to"] == "pitch")
        top += max(0.0, (min(1.0, max(p[1] for p in pts) + push) - 0.5) * 2.0 * bend_range(played))
    if played.get("osc2") and played.get("tones"):  # (OSC B tuned up, as far as the MOD tab can take it)
        top += max(0.0, osc2_most(played))
    return wave(hz, ppq, top)
