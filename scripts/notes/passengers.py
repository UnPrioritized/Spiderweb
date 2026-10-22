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


def placed(sh, rows, ppq):
    """The shape's passengers where its box is now: [(tick, track, tag, body)] in time order. rows = its unpacked
    notes (their last end = the box's length, as custom.block_notes)."""
    events = unpack_cc(sh["cc"])
    if not events or not len(rows):
        return []
    (b0, _), (b1, _), (b2, _) = sh["pts"]
    t_all = float(rows[:, 1].max())
    u = np.array([e[0] for e in events], float) / t_all
    beats = b0 + u * (b1 - b0)
    ticks = np.maximum(np.rint(beats * ppq), 0).astype(np.int64).tolist()
    out = [(t, e[1], e[2], e[3]) for t, e in zip(ticks, events)]
    out.sort(key=lambda e: e[0])  # (stable: a tick's events keep their order)
    return out
