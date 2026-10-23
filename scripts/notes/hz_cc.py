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
- they run on past the end of the sound (falls, echo, reverb: sound_span) for the Hz bass's run-on (ring_of: Hz
  bass settings, typed or Auto), so a wobble doesn't stop dead while the notes still ring;
- then every CC the lines used goes back to where players start it (CC_START; the Pitch wheel's bend range back to
  2 keys), so nothing is left on the channel for what plays on it after (user);
- a value is sent STEPS times a beat at most, only when it changes.
The Pitch wheel sends its bend range (RPN 0 = WHEEL_KEYS) before its first value.

Own channel per note (the Voice box's "CC" dropdown, own_channels; user 2026-10-21, like MPE in synths): each note
with what joins it (its slides, Legato, Glide) is a VOICE made as a Hz bass of its own (no chord lines merging),
on a channel of its own with its own CC lines (a line played once per note follows its own note). A voice is
busy from LEAD before its first note until the run-on after its sound and its lines' falls end (the lines' run-on), then
the next note may take it; MOST_VOICES busy at once = a new note takes the one that's been ringing longest (my pick:
the quietest). The notes and the messages take the SAME voices (voices strips the lines itself). An Arpeggio's steps
play on their held note's voice (user picked MPE's way, hunt 2026-10-21). Only in Multi channel (App.notes_tracks:
engine.one_voice otherwise); a Hz bass with no notes placed is one voice, its lines over its box (whole_tone)."""

import json
import math

import numpy as np

from notes.hzbass import (BOOST_FX, CC_DB, CC_FX, CC_NUMBER, CC_START, CC_TARGETS, RING_MOST, TREMOLO, TREMOLO_DEPTH,
                          WHEEL_KEYS, boosted, cc_midi, fx_at, glide_tones, has_cc, funnel_box, heard_layers, key_range,
                          left_edge, legato_links, links, live, note_span, own_channels, rack_tail, setting_at,
                          sound_span, tails, tempo_at, timed_line, without_cc)

STEPS = 96  # values a beat at most
LEAD = 0.05  # seconds the first values come before the first note
RING = 3.0  # seconds the lines run on past the end of the sound while Auto has measured nothing (no soundfont)
auto_ring = RING  # Auto's run-on: how long the soundfont's sound rings after a note (set_auto_ring)
CONTROL, BEND = 0xB0, 0xE0  # (MIDI status bytes, channel 0)
DEFAULT_BEND_RANGE = 2  # keys: the bend range players start with (put back at the end)
MOST_VOICES = 240  # voices at once: 15 channels a port x 16 ports (a 17th port is the 1st again)


def typed_ring(hz):
    """The run-on typed in the Hz bass settings (hz["ring"], seconds), None = Auto."""
    r = hz.get("ring") if isinstance(hz, dict) else None
    if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r):
        return None
    return max(0.0, min(RING_MOST, float(r)))


def ring_of(hz):
    """Seconds the CC lines run on past the end of the sound: the typed run-on, else Auto's."""
    r = typed_ring(hz)
    return auto_ring if r is None else r


def set_auto_ring(seconds):
    """Auto's run-on measured (None: not known, RING). True when it changed (the notes may change: voices)."""
    global auto_ring
    new = RING if seconds is None else max(0.0, min(RING_MOST, float(seconds)))
    if new == auto_ring:
        return False
    auto_ring = new
    return True


def cc_fall(hz):
    """Beats the heard layers' CC lines go on after a note at most (the falls of those with a sustain point): how much
    longer than its notes a voice's lines may sound (voices)."""
    fall = 0.0
    for l in heard_layers(hz):
        loop, off = l.get("loop") or {}, l.get("off") or ()
        fall = max([fall] + [loop[n] - at for n, at in (l.get("sustain") or {}).items()
                             if n in CC_FX and n not in off and n in loop])
    return fall


def per_second(hz, beat, bpm=None):
    """Beats a second at a beat from the Hz bass's left edge: the tempo it was made for there (its tempo line, D,
    else its one BPM: the run-on's seconds at the tempo it plays at, as App.cc_busy counts them; hunt 2026-10-23),
    else bpm (the caller's)."""
    return (tempo_at(hz, beat) if hz.get("tempo") or hz.get("bpm") or bpm is None else bpm) / 60.0


def voices(hz, left):
    """Own channel per note: (each heard layer as played (live: the Arpeggio's notes) without its CC lines, [(layer
    number, voice, tones of one note and what joins it)] in time order). Voices count from 0; each is one track.
    The same answer for the notes (engine.made_from: hz without its CC lines, hz["cc_own"] = {"fall": cc_fall})
    and the CC messages (the whole hz): a voice is busy until its notes AND its lines have ended. An Arpeggio's
    steps play on the channel of the note held (like an MPE synth: 3 notes held = 3 voices)."""
    own = hz.get("cc_own")
    fall = own["fall"] if isinstance(own, dict) else cc_fall(hz)
    ring = own.get("ring", auto_ring) if isinstance(own, dict) else ring_of(hz)
    hz = without_cc(hz)
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
            first = min(n["t"] for n in g)
            start = first - LEAD * per_second(hz, first)
            g_hz = dict(layer, tones=g)
            end = max(sound_span(g_hz), max(n["t"] + n["len"] for n in g) + fall + rack_tail(g_hz))
            groups.append((start, end + ring * per_second(hz, end) + 1.0 / STEPS, li, g))
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
    own = layers  # (each layer with its own notes: the CC boost goes by them; none placed = no boost)
    if not tones:  # (no notes placed: the shape plays across its box, so the lines run over it, as one note)
        tones = [whole_tone(sh)]
        layers = [dict(l, tones=tones) for l in layers]
    if voice:
        tones.sort(key=lambda n: n["t"])
        layers = [dict(l, tones=tones) if l.get("fx") else l for l in layers if l.get("tones") or l.get("fx")]
    lo, hi = min(n["t"] for n in tones), max(sound_span(l) for l in layers)
    first = left + lo - LEAD * per_second(hz, lo, bpm)  # (song beats)
    end = left + hi + ring_of(hz) * per_second(hz, hi, bpm)
    step = max(1, ppq // STEPS)
    t0 = max(0, math.floor(first * ppq))
    ticks = np.arange(t0, max(t0, math.ceil(end * ppq)) + 1, step, dtype=np.int64)
    beats = ticks / ppq - left
    out = []
    used = []
    boost = any(boosted(l) for l in own)
    for name in CC_FX:
        src = next((l for l in layers if name in (l.get("fx") or {})), None)
        if src is None and not (name == "cc_expression" and boost):
            continue
        used.append(name)
        if name == "cc_wheel":
            out += [(t0, *m) for m in bend_range(WHEEL_KEYS)]
        v = fx_at(src, name, beats) if src is not None else np.ones(len(beats))
        if name == "cc_expression" and boost:  # (louder / quieter by the boost: dB added, like two faders)
            v = np.where(v > 0, v + boost_db(hz, own, beats, bpm) / CC_DB, 0.0)
        vals = cc_midi(name, v)
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


def boost_db(hz, layers, beats, bpm=None):
    """The CC boost (each layer's hz["boost"], user 2026-10-23) at beats (an array from the left edge) in dB, for
    `layers` = the heard layers as played, each with its own notes (one voice's with Own channel per note), each
    effect times its boost:
    - Tremolo: the first layer boosting it, read from its newest note as its notes have it (one channel: like the
      lines); it waves on after the notes, through the run-on;
    - Volume: the LOUDEST note sounding (its own envelope, its fall after the note included; a layer not boosting
      its Volume counts as full while its notes sound), so no note is turned down by another's envelope (hunt
      2026-10-23). Where nothing sounds it fades from where the sound stopped by CC_DB over the run-on (ring_of),
      so a note's ring isn't cut dead (user: "notes just dead stop")."""
    beats = np.asarray(beats, float)
    db = np.zeros(len(beats))
    layers = [l for l in layers if l.get("tones")]
    if not len(beats) or not layers:
        return db
    src = next((l for l in layers if "tremolo" in boosted(l)), None)
    if src is not None:
        lfo = src.get("lfo") or {}
        rate = fx_at(src, "tremolo", beats) * TREMOLO  # (times a beat; turns counted from the newest note's start)
        turns = np.concatenate([[0.0], np.cumsum(0.5 * (rate[1:] + rate[:-1]) * np.diff(beats))])
        s0 = np.asarray(note_span(src, beats)[0], float)
        turns = turns - np.interp(s0, beats, turns)
        depth = setting_at(src, "tremolo_depth", lfo.get("tremolo_depth", TREMOLO_DEPTH), beats)
        tones = src["tones"]
        if timed_line(src, "tremolo_in", beats[:1], tones[0]) is not None:  # (Delay / Rise moved by the MOD tab:
            come = np.zeros(len(beats))  # each note as the notes have it, timed_line)
            heads = {}
            for n in tones:
                heads.setdefault(float(note_span(src, 0.0, n)[0]), n)
            for start, n in heads.items():
                at = s0 == start
                if at.any():
                    come[at] = timed_line(src, "tremolo_in", beats[at], n)
        else:
            since, wait, rise = beats - s0, lfo.get("tremolo_wait", 0.0), lfo.get("tremolo_rise", 0.0)
            come = np.clip((since - wait) / rise, 0.0, 1.0) if rise > 0 else (since >= wait - 1e-9).astype(float)
        f = 1.0 - depth * come * (1.0 - np.cos(2.0 * np.pi * turns)) / 2.0
        db += src["boost"]["tremolo"] * 20.0 * np.log10(np.maximum(f, 1e-6))
    if any("volume" in boosted(l) for l in layers):
        held = np.full(len(beats), -np.inf)  # (the loudest note held; ... the loudest fall after a note)
        fall = np.full(len(beats), -np.inf)
        for l in layers:
            amount = l["boost"]["volume"] if "volume" in boosted(l) else 0.0
            falls = tails(l)
            for n in l["tones"]:
                end = n["t"] + n["len"]
                a = int(np.searchsorted(beats, n["t"], "left"))
                m = int(np.searchsorted(beats, end, "right"))
                b = int(np.searchsorted(beats, end + falls.get(n["id"], 0.0), "right"))
                if b > a:
                    v = amount * 20.0 * np.log10(np.maximum(fx_at(l, "volume", beats[a:b], n), 1e-6)) if amount else 0.0
                    v = np.broadcast_to(v, (b - a,))
                    held[a:m] = np.maximum(held[a:m], v[:m - a])
                    fall[m:b] = np.maximum(fall[m:b], v[m - a:])
        on = np.isfinite(held)
        if on.any():
            first = int(np.argmax(on))
            last = np.maximum.accumulate(np.where(on, np.arange(len(beats)), -1))
            before = last < 0
            last = np.where(before, first, last)
            ring = ring_of(hz)
            if ring > 0:  # (after a note: where it was let go, fading by CC_DB over the run-on, or its fall if slower)
                sec = np.concatenate([[0.0], np.cumsum(np.diff(beats) / per_second(hz, beats[1:], bpm))])
                hold = held[last] - CC_DB / ring * np.maximum(0.0, sec - sec[last])
            else:
                hold = np.where(before, held[first], -np.inf)
            level = np.where(on, np.maximum(held, fall), np.maximum(fall, hold))
            db += np.maximum(level, -CC_DB * 2)
    return db


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
        if src.get("boost"):  # (the CC boost: what its effects do changes what it sends)
            parts.append([src["boost"], src.get("lfo"), src.get("mod")]
                         + [{k: v for k, v in (src.get(key) or {}).items() if k in BOOST_FX}
                            if isinstance(src.get(key), dict) else [k for k in src.get(key) or () if k in BOOST_FX]
                            for key in ("fx", "loop", "from", "sustain", "amount", "fit", "off")])
    parts.append([hz.get("cc_own"), ring_of(hz)])
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
    wanted = [s for messages in per_shape.values() for s in messages]
    rows = np.flatnonzero(np.isin(slots, wanted))  # (one pass over the song, not one per slot)
    for i, messages in per_shape.items():
        for s, events in messages.items():
            on = rows[slots[rows] == s]
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


def sharers(rendered, i, messages, use10, ports=0):
    """The other shapes (their numbers) with notes on a channel shape i's CC messages ({slot: messages}) go on, while
    they're sent: what its CC lines change too (the Hz window's warning). ports = how many ports tell slots apart
    (engine.PORTS when the file writes them, a player's; 0 = one port: slots 15 apart share a channel)."""
    per = 16 if use10 else 15
    messages = {s: events for s, events in messages.items() if events}
    if not messages:
        return set()
    rendered = np.asarray(rendered).reshape(-1, 6)
    first, last = min(e[0][0] for e in messages.values()), max(e[-1][0] for e in messages.values())
    # (the notes sounding while any of its lines are sent, first: a big song took ~1 s per edit)
    rendered = rendered[(rendered[:, 0] < last) & (rendered[:, 1] > first) & (rendered[:, 5] != i)]
    slots = rendered[:, 4]
    out = set()
    for s, events in messages.items():
        on = slots % per == s % per
        if ports:
            on &= slots // per % ports == s // per % ports
        m = on & (rendered[:, 0] < events[-1][0]) & (rendered[:, 1] > events[0][0])
        out.update(np.unique(rendered[m, 5]).tolist())
    return out


KEPT_MOST = 64  # shapes' CC messages remembered (remembered)
_kept = {}


def remembered(make, sh, ppq, bpm):
    """make(sh, ppq, bpm) (cc_events / voice_events), remembered for the same shape, PPQ and BPM: playback asks again
    at every edit while playing, and a long Arpeggio's took over a second (the window froze). Don't change what it
    gives back."""
    hz = sh["hz"]
    key = (make.__name__, ppq, bpm, auto_ring, json.dumps(dict(sh, hz={k: v for k, v in hz.items() if k != "_memo"}),
                                              sort_keys=True, default=repr))
    got = _kept.pop(key, None)
    if got is None:
        got = make(sh, ppq, bpm)
    _kept[key] = got  # (last = used most recently)
    while len(_kept) > KEPT_MOST:
        del _kept[next(iter(_kept))]
    return got


def cc_spans(sh, ppq, bpm):
    """{track: (first tick, last tick)} a Hz bass's CC messages are sent over, the run-on and the values put back
    included (None = every track: one channel; Own channel per note: each voice's), {} without CC lines. Multi
    channel split by time counts it as sounding then, so no other shape takes its channel while its lines change it
    (engine.assign_slots extra; user 2026-10-21)."""
    hz = sh.get("hz")
    if not isinstance(hz, dict) or not has_cc(hz):
        return {}
    if own_channels(hz):
        return {v: (e[0][0], e[-1][0]) for v, e in remembered(voice_events, sh, ppq, bpm).items() if e}
    e = remembered(cc_events, sh, ppq, bpm)
    return {None: (e[0][0], e[-1][0])} if e else {}


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
