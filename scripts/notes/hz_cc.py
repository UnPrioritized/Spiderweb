"""Hz bass CC lines (hz_settings.CC_FX) as MIDI messages: Cutoff (CC 74), Expression (CC 11), Channel volume (CC 7),
Pan (CC 10) and Pitch wheel (pitch bend). They make no notes (the notes are made without them: without_cc); a
MIDI player applies them to the WHOLE channel, so they go on every channel the Hz bass's notes are on (a layer's
notes clashing with another's are on another channel: they get them too).

One channel for the whole Hz bass: each line is read over the whole Hz bass (fx_at with no tone: a line played once
per note follows the newest note, like the synth window's FX tab). Layers: each CC line comes from the first heard
layer that has it.

Rules (measured with several players before it was built, dev/data/planned-features.md "CC lines for Hz bass"):
- the values start LEAD seconds before the first note (a player that glides to a new value would glide during the
  first note otherwise), at tick 0 at the earliest;
- they run on RING seconds past the end of the sound (falls, echo, reverb: sound_span), so a wobble doesn't stop
  dead while the notes still ring;
- then every CC the lines used goes back to where players start it (CC_START; the Pitch wheel's bend range back to
  2 keys), so nothing is left on the channel for what plays on it after (user);
- a value is sent STEPS times a beat at most, only when it changes.
The Pitch wheel sends its bend range (RPN 0 = WHEEL_KEYS) before its first value.

Own channel per note (the Voice box's "CC" dropdown, own_channels; user 2026-10-21, like MPE in synths): each note
with what joins it (its slides, Legato, Glide) is a VOICE made as a Hz bass of its own (no chord lines merging),
on a channel of its own with its own CC lines (a line played once per note follows its own note). A voice is
busy from LEAD before its first note until RING after its sound and its lines' falls end (the lines' run-on), then
the next note may take it; MOST_VOICES busy at once = a new note takes the one that's been ringing longest (my pick:
the quietest). The notes and the messages take the SAME voices (voices strips the lines itself). An Arpeggio's steps
play on their held note's voice (user picked MPE's way, hunt 2026-10-21). Only in Multi channel (App.notes_tracks:
engine.one_voice otherwise); a Hz bass with no notes placed is one voice, its lines over its box (whole_tone)."""

import json
import math

import numpy as np

from notes.hzbass import (CC_FX, CC_NUMBER, CC_START, CC_TARGETS, WHEEL_KEYS, cc_midi, fx_at, glide_tones, has_cc,
                          funnel_box, heard_layers, key_range, left_edge, legato_links, links, live, own_channels,
                          rack_tail, sound_span, without_cc)

STEPS = 96  # values a beat at most
LEAD = 0.05  # seconds the first values come before the first note
RING = 3.0  # seconds the lines run on past the end of the sound
CONTROL, BEND = 0xB0, 0xE0  # (MIDI status bytes, channel 0)
DEFAULT_BEND_RANGE = 2  # keys: the bend range players start with (put back at the end)
MOST_VOICES = 240  # voices at once: 15 channels a port x 16 ports (a 17th port is the 1st again)


def cc_fall(hz):
    """Beats the heard layers' CC lines go on after a note at most (the falls of those with a sustain point): how much
    longer than its notes a voice's lines may sound (voices)."""
    fall = 0.0
    for l in heard_layers(hz):
        loop, off = l.get("loop") or {}, l.get("off") or ()
        fall = max([fall] + [loop[n] - at for n, at in (l.get("sustain") or {}).items()
                             if n in CC_FX and n not in off and n in loop])
    return fall


def voices(hz, left):
    """Own channel per note: (each heard layer as played (live: the Arpeggio's notes) without its CC lines, [(layer
    number, voice, tones of one note and what joins it)] in time order). Voices count from 0; each is one track.
    The same answer for the notes (engine.made_from: hz without its CC lines, hz["cc_own"] = {"fall": cc_fall})
    and the CC messages (the whole hz): a voice is busy until its notes AND its lines have ended. An Arpeggio's
    steps play on the channel of the note held (like an MPE synth: 3 notes held = 3 voices)."""
    own = hz.get("cc_own")
    fall = own["fall"] if isinstance(own, dict) else cc_fall(hz)
    hz = without_cc(hz)
    per_beat = float(hz["bpm"]) / 60.0  # (beats a second)
    played, groups = [], []
    for li, layer in enumerate(heard_layers(hz)):
        layer = live(layer, left)
        played.append(layer)
        tones = layer.get("tones") or ()
        top = {n["id"]: n["id"] for n in tones}
        top.update({("held", n["held_by"]): ("held", n["held_by"]) for n in tones if "held_by" in n})

        def root(i):
            while top[i] != i:
                top[i] = top[top[i]]
                i = top[i]
            return i
        pairs = ([(a["id"], b["id"]) for a, b, _ in links(tones)]
                 + [(a["id"], b["id"]) for a, b in legato_links(layer.get("voice"), tones)]
                 + [(a["id"], b) for b, got in glide_tones(layer).items() for a in got]
                 + [(n["id"], ("held", n["held_by"])) for n in tones if "held_by" in n])
        for a, b in pairs:
            top[root(a)] = root(b)
        joined = {}
        for n in tones:
            joined.setdefault(root(n["id"]), []).append(n)
        for g in joined.values():
            start = min(n["t"] for n in g) - LEAD * per_beat
            g_hz = dict(layer, tones=g)
            end = max(sound_span(g_hz), max(n["t"] + n["len"] for n in g) + fall + rack_tail(g_hz))
            groups.append((start, end + RING * per_beat + 1.0 / STEPS, li, g))
    groups.sort(key=lambda g: g[0])
    busy, out = [], []  # (each voice: the beat it's free again)
    for start, end, li, g in groups:
        v = next((v for v, b in enumerate(busy) if b <= start + 1e-9), None)
        if v is None and len(busy) < MOST_VOICES:
            v = len(busy)
            busy.append(end)
        elif v is None:
            v = min(range(len(busy)), key=busy.__getitem__)
        busy[v] = max(busy[v], end)
        out.append((li, v, g))
    return played, out


def voice_hz(played, li, tones):
    """One voice's tones of layer li as a Hz bass of their own (voices)."""
    out = {k: v for k, v in played[li].items() if k not in ("cc_own", "layers", "layer", "_memo")}
    out["tones"] = sorted(tones, key=lambda n: n["t"])
    return out


def bend_range(keys):
    """The messages setting the pitch bend range to `keys` whole keys (RPN 0, then the RPN closed again so a later
    Data entry changes nothing): [(status, number, value)]."""
    return [(CONTROL, 101, 0), (CONTROL, 100, 0), (CONTROL, 6, keys), (CONTROL, 38, 0), (CONTROL, 101, 127),
            (CONTROL, 100, 127)]


def message(name, value):
    """(status, data 1, data 2) of a CC line's MIDI value."""
    if name == "cc_wheel":
        return BEND, value & 0x7F, value >> 7
    return CONTROL, CC_NUMBER[name], value


def cc_events(sh, ppq, bpm, layers=None):
    """A Hz bass shape's CC lines as [(tick, status, data 1, data 2)] in time order (status without the channel),
    [] when it has none (or no notes). Messages on one tick stay in their order (the bend range before its value).
    layers = one voice's (each heard layer as played with only the voice's tones: own channel per note): its lines
    follow the voice's own notes, whichever layer they're in."""
    hz = sh.get("hz")
    if sh.get("kind") is None or not isinstance(hz, dict) or not has_cc(hz):
        return []
    left = left_edge(sh)
    voice = layers is not None
    if not voice:
        layers = [live(l, left) for l in heard_layers(hz)]
    tones = [n for l in layers for n in l.get("tones") or ()]
    if not tones and voice:
        return []
    if not tones:  # (no notes placed: the shape plays across its box, so the lines run over it, as one note)
        tones = [whole_tone(sh)]
        layers = [dict(l, tones=tones) for l in layers]
    if voice:
        tones.sort(key=lambda n: n["t"])
        layers = [dict(l, tones=tones) if l.get("fx") else l for l in layers if l.get("tones") or l.get("fx")]
    per_beat = bpm / 60.0  # (beats a second)
    first = left + min(n["t"] for n in tones) - LEAD * per_beat  # (song beats)
    end = left + max(sound_span(l) for l in layers) + RING * per_beat
    step = max(1, ppq // STEPS)
    t0 = max(0, math.floor(first * ppq))
    ticks = np.arange(t0, max(t0, math.ceil(end * ppq)) + 1, step, dtype=np.int64)
    beats = ticks / ppq - left
    out = []
    used = []
    for name in CC_FX:
        src = next((l for l in layers if name in (l.get("fx") or {})), None)
        if src is None:
            continue
        used.append(name)
        if name == "cc_wheel":
            out += [(t0, *m) for m in bend_range(WHEEL_KEYS)]
        vals = cc_midi(name, fx_at(src, name, beats))
        keep = np.ones(len(vals), bool)
        keep[1:] = vals[1:] != vals[:-1]
        out += [(t, *message(name, v)) for t, v in zip(ticks[keep].tolist(), vals[keep].tolist())]
    last = int(ticks[-1]) + step  # back to where players start, once the lines have ended
    for name in used:
        if name == "cc_wheel":
            out += [(last, *message(name, CC_START[name]))] + [(last, *m) for m in bend_range(DEFAULT_BEND_RANGE)]
        else:
            out.append((last, *message(name, CC_START[name])))
    out.sort(key=lambda e: e[0])  # (stable: a tick's messages keep their order)
    return out


def whole_tone(sh):
    """A Hz bass with no notes placed (the side panel's tick, a funnel) as the one note it plays: across its box (a
    funnel: its lines and wall), from its left edge."""
    if sh["kind"] == "funnel":
        b0, b1, _, _ = funnel_box(sh)
    else:
        (a, _), (b, _), (c, _) = sh["pts"]
        b0, b1 = min(a, b, c, b + c - a), max(a, b, c, b + c - a)
    lo, hi = key_range(sh)
    return {"t": 0.0, "len": max(b1 - b0, 1.0 / STEPS), "key": (lo + hi) // 2, "cents": 0.0, "id": 1, "to": []}


def cc_key(hz):
    """A short text that changes when what the CC lines send may change while the notes stay the same (the lines,
    their repeats / sustain / bypass, MOD links to them, the Voice box's Legato): for the preview's "anything new?"."""
    if not isinstance(hz, dict) or not has_cc(hz):
        return ""
    parts = []
    for src in [hz] + [e for e in hz.get("layers") or () if isinstance(e, dict)]:
        parts.append([{k: v for k, v in (src.get(key) or {}).items() if k in CC_FX} if isinstance(src.get(key), dict)
                      else [k for k in src.get(key) or () if k in CC_FX]
                      for key in ("fx", "loop", "from", "sustain", "amount", "fit", "off")]
                     + [src.get("mod") if any(l.get("to") in CC_TARGETS for l in (src.get("mod") or {}).get("links", ()))
                        else None, src.get("voice"), (src.get("solo"), src.get("mute")), src.get("filter"),
                        (src.get("bypass") or {}).get("boxes")])
    parts.append(hz.get("cc_own"))
    return json.dumps(parts, sort_keys=True)


def voice_events(sh, ppq, bpm):
    """Own channel per note: {voice: its CC messages} (cc_events of each voice's own notes)."""
    left = left_edge(sh)
    played, got = voices(sh["hz"], left)
    played = [live(l, left) for l in heard_layers(sh["hz"])]  # (with their CC lines: voices' are without)
    mine = {}
    for li, v, tones in got:
        mine.setdefault(v, [[] for _ in played])[li].extend(tones)
    return {v: cc_events(sh, ppq, bpm, [dict(l, tones=t) for l, t in zip(played, per)]) for v, per in mine.items()}


def note_cc(sh, ppq, bpm, starts, tracks=None):
    """What each note's channel holds where it starts (ticks; tracks = the voices with own channel per note), for
    the live keys' quick sound: (rows of (Cutoff, Pan, pitch bend) MIDI values, gains from Channel volume x
    Expression along the General MIDI curve, 1 at the players' start values), or (None, None) without CC lines."""
    hz = sh.get("hz")
    if not isinstance(hz, dict) or not has_cc(hz):
        return None, None
    own = own_channels(hz) and tracks is not None
    lists = voice_events(sh, ppq, bpm) if own else {None: cc_events(sh, ppq, bpm)}
    starts = np.asarray(starts, np.int64)
    names = ("cc_cutoff", "cc_pan", "cc_wheel", "cc_volume", "cc_expression")
    vals = {name: np.full(len(starts), CC_START[name], np.int64) for name in names}
    number = {n: name for name, n in CC_NUMBER.items()}
    for v, events in lists.items():
        at = np.ones(len(starts), bool) if v is None else np.asarray(tracks) == v
        for name in names:
            got = [(t, a | b << 7) if s == BEND else (t, b) for t, s, a, b in events
                   if (name == "cc_wheel") == (s == BEND) and (s == BEND or number.get(a) == name)]
            if got:
                ticks, values = np.array(got, np.int64).T
                i = np.searchsorted(ticks, starts[at], "right") - 1
                vals[name][at] = np.where(i >= 0, values[np.maximum(i, 0)], CC_START[name])
    gain = (vals["cc_volume"] / CC_START["cc_volume"]) ** 2 * (vals["cc_expression"] / CC_START["cc_expression"]) ** 2
    return np.stack([vals["cc_cutoff"], vals["cc_pan"], vals["cc_wheel"]], axis=1), gain


def cc_apart(rendered, per_shape, use10, ports=0):
    """PLAYBACK ONLY (user 2026-10-21: other shapes on the piano roll heard a Hz bass's CC lines; files keep their
    channels): each Hz bass's CC slot that other shapes' notes are on too goes to a slot of its own whose channel
    (and port, ports = the player's) nothing else plays on; none free = left shared. per_shape = {shape's number:
    slot_events of it alone}. Returns (rendered with the moved notes (a copy, only when some move), {slot:
    messages})."""
    per = 16 if use10 else 15  # (slots a round of channels: engine.CHANNELS without channel 10)

    def place(slot):
        return slot // per % ports if ports else 0, slot % per
    rendered = np.asarray(rendered).reshape(-1, 6)
    slots, owners = rendered[:, 4], rendered[:, 5]
    used = {place(s) for s in np.unique(slots).tolist()}
    nxt = int(slots.max()) + 1 if len(rendered) else 0
    out, moves = {}, []
    for i, messages in per_shape.items():
        for s, events in messages.items():
            on = np.flatnonzero(slots == s)
            if moves:  # (notes moved off it already: no longer there)
                on = on[~np.isin(on, np.concatenate([m for m, _ in moves]))]
            mine = on[owners[on] == i]
            if len(mine) < len(on):
                free = next((n for n in range(nxt, nxt + per * max(1, ports)) if place(n) not in used), None)
                if free is not None:
                    used.add(place(free))
                    nxt = free + 1
                    moves.append((mine, free))
                    s = free
            out.setdefault(s, []).extend(events)
    if moves:
        rendered = np.array(rendered, np.int64)
        for mine, free in moves:
            rendered[mine, 4] = free
    for events in out.values():
        events.sort(key=lambda e: e[0])
    return rendered, out


KEPT_MOST = 64  # shapes' CC messages remembered (remembered)
_kept = {}


def remembered(make, sh, ppq, bpm):
    """make(sh, ppq, bpm) (cc_events / voice_events), remembered for the same shape, PPQ and BPM: playback asks again
    at every edit while playing, and a long Arpeggio's took over a second (the window froze). Don't change what it
    gives back."""
    hz = sh["hz"]
    key = (make.__name__, ppq, bpm, json.dumps(dict(sh, hz={k: v for k, v in hz.items() if k != "_memo"}),
                                              sort_keys=True, default=repr))
    got = _kept.pop(key, None)
    if got is None:
        got = make(sh, ppq, bpm)
    _kept[key] = got  # (last = used most recently)
    while len(_kept) > KEPT_MOST:
        del _kept[next(iter(_kept))]
    return got


def slot_events(shapes, rendered, ppq, bpm, only=None, voice_slots=None):
    """{slot: [(tick, status, data 1, data 2)]}: every Hz bass's CC messages on each slot (= channel, track) its
    notes are on in rendered ((start, end, pitch, velocity, slot, owner) rows, owner = the shape's number). only =
    the shapes' numbers to take (None: all). Several shapes on one slot: their messages merged in time order.
    voice_slots = {shape's number: {voice: slot}} (render's, Multi channel): a Hz bass with its own channel per note
    sends each voice's lines on that voice's slot only; without one (Single channel) as one channel."""
    out = {}
    if not len(rendered):
        return out
    rendered = np.asarray(rendered)
    for i, sh in enumerate(shapes):
        if only is not None and i not in only or not has_cc(sh.get("hz") or {}):
            continue
        slots = (voice_slots or {}).get(i)
        if slots is not None and own_channels(sh["hz"]):
            for v, events in remembered(voice_events, sh, ppq, bpm).items():
                if events and v in slots:
                    out.setdefault(int(slots[v]), []).extend(events)
            continue
        events = remembered(cc_events, sh, ppq, bpm)
        if not events:
            continue
        for slot in np.unique(rendered[rendered[:, 5] == i, 4]).tolist():
            out.setdefault(int(slot), []).extend(events)
    for events in out.values():
        events.sort(key=lambda e: e[0])
    return out
