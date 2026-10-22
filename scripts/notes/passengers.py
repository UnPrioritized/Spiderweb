"""CC passengers (user 2026-10-22): the other events on the tracks pasted from Domino (control changes, pitch bend,
program changes, anything else Domino keeps on a track) ride along with the pasted notes, so a round trip Domino ->
Spiderweb -> Domino loses nothing. Each is kept as Domino stored it (its item after the position), all packed into
one text on the shape (sh["cc"]), ticks counted from the shape's first note like its notes (custom.notes_shape), so
moving / stretching / flipping the box carries them (placed). Copy / Export to Domino write them back as they were;
Export MIDI and playback send the ones a MIDI file has (midi_messages: CCs, pitch bend, channel pressure, program
changes), the rest is Domino's own (left out, counted)."""

import base64
import binascii
import struct
import zlib

import numpy as np

from files.unzip import unzip

HEAD = struct.Struct("<qIHI")  # tick, track, Domino's event tag, body length
CONTROL, PROGRAM = 2003, 2002  # (Domino's event tags: a control event (CCM) and a program change)
PITCH_BEND, PRESSURE = 130, 132  # (CCM numbers; 0..127 = that CC)
_unpacked = {}


def pack_cc(events):
    """[(tick, track, tag, body)] -> the text kept on the shape (zlib, base64)."""
    raw = b"".join(HEAD.pack(int(t), int(k), int(tag), len(b)) + bytes(b) for t, k, tag, b in events)
    return base64.b64encode(zlib.compress(raw, 1)).decode("ascii")


def unpack_cc(text):
    """pack_cc's text -> ((tick, track, tag, body), ...) (remembered). ValueError / zlib.error if damaged."""
    got = _unpacked.get(text)
    if got is None:
        if len(_unpacked) > 20:
            _unpacked.clear()
        raw = unzip(base64.b64decode(text, validate=True), key=text)
        got, i = [], 0
        while i < len(raw):
            if i + HEAD.size > len(raw):
                raise ValueError("cut off")
            t, k, tag, n = HEAD.unpack_from(raw, i)
            i += HEAD.size
            if i + n > len(raw):
                raise ValueError("cut off")
            got.append((t, k, tag, raw[i:i + n]))
            i += n
        got = tuple(got)
        _unpacked[text] = got
    return got


def check_cc(text):
    """True if text is packed passengers Spiderweb can use."""
    try:
        return len(unpack_cc(text)) > 0
    except (TypeError, ValueError, binascii.Error, zlib.error):
        return False


def has_cc(sh):
    return isinstance(sh.get("cc"), str)


def _fields(body):
    """{tag: bytes} of an event's items."""
    out, i = {}, 0
    while i + 6 <= len(body):
        tag, n = struct.unpack_from("<HI", body, i)
        out.setdefault(tag, body[i + 6:i + 6 + n])
        i += 6 + n
    return out


def _value(raw):
    """A value list's first f32 (Domino's numbers in control events), or None."""
    f = _fields(raw).get(0, b"")
    return struct.unpack("<f", f)[0] if len(f) == 4 else None


def midi_messages(tag, body):
    """A passenger as MIDI messages [(status without the channel, data 1, data 2)]: [] when a MIDI file has no such
    message (Domino's own events, its macros). CC values as stored (measured: Domino keeps them 0-127 as is), pitch
    bend from its middle; a program change's bank (when Domino set one) as CC 0 / 32 first."""
    f = _fields(body)
    if tag == CONTROL and len(f.get(2001, b"")) == 2:
        ccm, v = struct.unpack("<H", f[2001])[0], _value(f.get(2003, b""))
        if v is None or v != v:
            return []
        v = int(round(v))
        if ccm < 128:
            return [(0xB0, ccm, max(0, min(127, v)))]
        if ccm == PITCH_BEND:
            v = max(0, min(16383, v + 8192))
            return [(0xE0, v & 127, v >> 7)]
        if ccm == PRESSURE:
            return [(0xD0, max(0, min(127, v)), 0)]
        return []
    if tag == PROGRAM and len(f.get(2003, b"")) == 1:
        out = []
        for k, cc in ((2001, 0), (2002, 32)):  # (bank MSB / LSB: -1 = none)
            b = f.get(k, b"")
            if len(b) == 1 and struct.unpack("<b", b)[0] >= 0:
                out.append((0xB0, cc, b[0]))
        return out + [(0xC0, max(0, min(127, f[2003][0] - 1)), 0)]
    return []


def with_value(body, v):
    """A control event's body with its value (the first f32 of its value list) set to v."""
    out, i = bytearray(body), 0
    while i + 6 <= len(out):
        tag, n = struct.unpack_from("<HI", out, i)
        if tag == 2003 and n >= 10:
            struct.pack_into("<f", out, i + 12, float(v))
            break
        i += 6 + n
    return bytes(out)


def _ccm(tag, body):
    """A control event's CCM number (0-127 = that CC), else None."""
    f = _fields(body).get(2001, b"") if tag == CONTROL else b""
    return struct.unpack("<H", f)[0] if len(f) == 2 else None


# what a MIDI player starts a channel at (GM; Domino keeps CCs as is, pitch bend from its middle)
USUAL = {7: 100, 10: 64, 11: 127, 91: 40, PITCH_BEND: 0}
STAY = (0, 32)  # (bank select: stays at the start with the program change on a flip)
NO_USUAL = (6, 38, 96, 97, 98, 99, 100, 101) + tuple(range(120, 128))  # (data entry, RPN / NRPN, mode messages)


def placed(sh, rows, ppq):
    """The shape's passengers where its box is now: [(tick, track, tag, body)] in time order. rows = its unpacked
    notes (their last end = the box's length, as custom.block_notes). The box's time side carries them: moved /
    stretched along it; flipped sideways (that side runs back) = mirrored by the user's rule (mirrored); a turned
    or slanted box spreads them over the time it covers, the way its time side runs (user: turns leave them)."""
    events = unpack_cc(sh["cc"])
    if not events or not len(rows):
        return []
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    t_all = float(rows[:, 1].max())
    u = np.array([e[0] for e in events], float) / t_all
    ub = b1 - b0
    if abs(b2 - b0) > 1e-9 or abs(p1 - p0) > 1e-9:  # (turned / slanted: over the box's whole time, its way)
        corners = [b0, b1, b2, b1 + b2 - b0]
        lo, hi = min(corners), max(corners)
        b0, ub = (lo, hi - lo) if ub >= 0 else (hi, lo - hi)
    if ub < 0:
        return mirrored(events, u, b0, ub, ppq)
    beats = b0 + u * ub
    ticks = np.maximum(np.rint(beats * ppq), 0).astype(np.int64).tolist()
    out = [(t, e[1], e[2], e[3]) for t, e in zip(ticks, events)]
    out.sort(key=lambda e: e[0])  # (stable: a tick's events keep their order)
    return out


def shared_out(sh, parts, ppq):
    """A pasted shape cut into parts ((start, length, key, velocity, track) tick rows as they sound now, slice.py):
    each part's passengers packed (or None), ticks from its first note. By time per track (user 2026-10-22): an
    event goes to the part playing that track there (its latest note start at or before it), else the part where
    the track starts first; a track no part has: the first part."""
    from notes.custom import unpack_notes
    events = placed(sh, unpack_notes(sh["notes"]), ppq)
    mine = [[] for _ in parts]
    starts = {}
    for p, n in enumerate(parts):
        for k in np.unique(n[:, 4]).tolist():
            starts.setdefault(k, []).append((p, np.sort(n[n[:, 4] == k, 0])))
    for t, k, tag, body in events:
        best = None
        for p, s in starts.get(k, ()):
            i = int(np.searchsorted(s, t, "right"))
            key = (1, int(s[i - 1])) if i else (0, -int(s[0]))  # (playing there wins, the latest start; else the first)
            if best is None or key > best[0]:
                best = (key, p)
        mine[best[1] if best else 0].append((t, k, tag, body))
    return [pack_cc([(t - int(n[:, 0].min()), k, tag, b) for t, k, tag, b in got]) if got else None
            for got, n in zip(mine, parts)]


def carried(shapes, voice_slots, got, ppq):
    """Rendered rows (start, end, pitch, velocity, slot, owner) copied into a new notes shape whose tracks are their
    slots (Multi channel: Apply mask, Gate sensitive merge, Turn into plain notes): the passengers of the pasted
    shapes they came from (user: kept with their notes), each on the slot its track had, those inside the time the
    copy holds notes of that slot, at their own ticks. Packed, ticks from the copy's first note; None if none."""
    from notes.custom import unpack_notes
    got = np.asarray(got, np.int64)
    if not len(got):
        return None
    out, t0 = [], int(got[:, 0].min())
    for o in np.unique(got[:, 5]).tolist():
        sh, slots = shapes[o], (voice_slots or {}).get(o)
        if "cc" not in sh or not slots:
            continue
        mine = got[got[:, 5] == o]
        span = {s: (int(mine[mine[:, 4] == s, 0].min()), int(mine[mine[:, 4] == s, 1].max()))
                for s in np.unique(mine[:, 4]).tolist()}
        for t, k, tag, body in placed(sh, unpack_notes(sh["notes"]), ppq):
            s = slots.get(k)
            if s in span and span[s][0] <= t <= span[s][1]:
                out.append((t - t0, s, tag, body))
    out.sort(key=lambda e: e[0])
    return pack_cc(out) if out else None


def mirrored(events, u, b0, ub, ppq):
    """placed for a box flipped sideways (user 2026-10-22): each control's values mirrored as STRETCHES (a value
    held from its event to the next one runs the other way, so it now starts where that stretch ends; the value
    held to the shape's end starts at its start; the value before the first event, the usual one, comes after
    where the first one was); program changes and bank select stay at the start, in their order; anything else
    simply mirrored."""
    tick = lambda uu: max(0, int(round((b0 + uu * ub) * ppq)))
    end_u = max(1.0, float(u.max()))
    start = tick(end_u)  # (the shape's start: the far end before the flip)
    first, out, runs = [], [], {}
    for k, e in enumerate(events):
        ccm = _ccm(e[2], e[3])
        if e[2] == PROGRAM or ccm in STAY:
            first.append((start, e[1], e[2], e[3]))
        elif ccm is not None:
            runs.setdefault((e[1], ccm), []).append(k)
        else:
            out.append((tick(u[k]), e[1], e[2], e[3]))
    for (track, ccm), ks in runs.items():
        ks.sort(key=lambda k: u[k])
        for j, k in enumerate(ks):  # (its value now from where the stretch it held ended)
            out.append((tick(u[ks[j + 1]]) if j + 1 < len(ks) else start, track, events[k][2], events[k][3]))
        if ccm not in NO_USUAL:  # (before it: the usual value, from where the first one was)
            out.append((tick(u[ks[0]]), track, events[ks[0]][2], with_value(events[ks[0]][3], USUAL.get(ccm, 0))))
    out.sort(key=lambda e: e[0])
    return first + out
