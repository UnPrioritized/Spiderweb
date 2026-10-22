"""CC passengers (user 2026-10-22): the other events on the tracks pasted from Domino (control changes, pitch bend,
program changes, anything else Domino keeps on a track) ride along with the pasted notes, so a round trip Domino ->
Spiderweb -> Domino loses nothing. Each is kept as Domino stored it (its item after the position), all packed into
one text on the shape (sh["cc"]), ticks counted from the shape's first note like its notes (custom.notes_shape), so
moving / stretching / flipping the box carries them (placed). Copy / Export to Domino write them back as they were;
Export MIDI and playback send the ones a MIDI file has (midi_messages: CCs, pitch bend, channel pressure, program
changes), the rest is Domino's own (left out, counted).
Worked out on whole arrays (hunt 2026-10-22: a million events took seconds on every export); unpacked texts are
remembered up to MOST_KEPT events in all."""

import base64
import binascii
import functools
import struct
import zlib
from collections import OrderedDict

import numpy as np

from files.unzip import unzip

HEAD = struct.Struct("<qIHI")  # tick, track, Domino's event tag, body length
CONTROL, PROGRAM = 2003, 2002  # (Domino's event tags: a control event (CCM) and a program change)
PITCH_BEND, PRESSURE = 130, 132  # (CCM numbers; 0..127 = that CC)
MOST_KEPT = 4_000_000  # (events of all the unpacked texts remembered together: ~1 GB at most)
_unpacked = OrderedDict()


class Rides:
    """One shape's passengers unpacked: events ((tick, track, tag, body), ...) + ticks / tracks / tags / ccm
    (control events' CCM number, -1 for the rest) as arrays."""

    def __init__(self, events):
        self.events = events
        self.ticks = np.fromiter((e[0] for e in events), np.int64, len(events))
        self.tracks = np.fromiter((e[1] for e in events), np.int64, len(events))
        self.tags = np.fromiter((e[2] for e in events), np.int64, len(events))
        self.ccm = np.fromiter((_ccm(e[2], e[3]) for e in events), np.int64, len(events))


def pack_cc(events):
    """[(tick, track, tag, body)] -> the text kept on the shape (zlib, base64)."""
    raw = b"".join(HEAD.pack(int(t), int(k), int(tag), len(b)) + bytes(b) for t, k, tag, b in events)
    return base64.b64encode(zlib.compress(raw, 1)).decode("ascii")


def rides_of(text):
    """pack_cc's text -> its Rides (remembered). ValueError / zlib.error if damaged."""
    got = _unpacked.get(text)
    if got is not None:
        _unpacked.move_to_end(text)
        return got
    raw = unzip(base64.b64decode(text, validate=True), key=text)
    events, i = [], 0
    while i < len(raw):
        if i + HEAD.size > len(raw):
            raise ValueError("cut off")
        t, k, tag, n = HEAD.unpack_from(raw, i)
        i += HEAD.size
        if i + n > len(raw):
            raise ValueError("cut off")
        events.append((t, k, tag, raw[i:i + n]))
        i += n
    got = _unpacked[text] = Rides(tuple(events))
    while len(_unpacked) > 1 and sum(len(r.events) for r in _unpacked.values()) > MOST_KEPT:
        _unpacked.popitem(last=False)  # (the least used first)
    return got


def unpack_cc(text):
    """pack_cc's text -> ((tick, track, tag, body), ...)."""
    return rides_of(text).events


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


@functools.lru_cache(maxsize=1 << 16)  # (the same CC + value comes again and again)
def midi_messages(tag, body):
    """A passenger as MIDI messages ((status without the channel, data 1, data 2), ...): () when a MIDI file has no
    such message (Domino's own events, its macros). CC values as stored (measured: Domino keeps them 0-127 as is),
    pitch bend from its middle; a program change's bank (when Domino set one) as CC 0 / 32 first."""
    f = _fields(body)
    if tag == CONTROL and len(f.get(2001, b"")) == 2:
        ccm, v = struct.unpack("<H", f[2001])[0], _value(f.get(2003, b""))
        if v is None or v != v:
            return ()
        v = int(round(v))
        if ccm < 128:
            return ((0xB0, ccm, max(0, min(127, v))),)
        if ccm == PITCH_BEND:
            v = max(0, min(16383, v + 8192))
            return ((0xE0, v & 127, v >> 7),)
        if ccm == PRESSURE:
            return ((0xD0, max(0, min(127, v)), 0),)
        return ()
    if tag == PROGRAM and len(f.get(2003, b"")) == 1:
        out = []
        for k, cc in ((2001, 0), (2002, 32)):  # (bank MSB / LSB: -1 = none)
            b = f.get(k, b"")
            if len(b) == 1 and struct.unpack("<b", b)[0] >= 0:
                out.append((0xB0, cc, b[0]))
        return tuple(out) + ((0xC0, max(0, min(127, f[2003][0] - 1)), 0),)
    return ()


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
    """A control event's CCM number (0-127 = that CC), else -1."""
    f = _fields(body).get(2001, b"") if tag == CONTROL else b""
    return struct.unpack("<H", f)[0] if len(f) == 2 else -1


# what a MIDI player starts a channel at (GM; Domino keeps CCs as is, pitch bend from its middle)
# (only these: a control with no known start value is never given one; Domino's macros neither, hunt 2026-10-22:
# 0 for brightness muffled the sound)
USUAL = {**dict.fromkeys((1, 2, 4, 5, 64, 65, 66, 67, 68, 69, 84, 92, 93, 94, 95), 0),
         **dict.fromkeys((8, 10, 71, 72, 73, 74, 75, 76, 77, 78, 79), 64), 7: 100, 11: 127, 91: 40, PITCH_BEND: 0,
         PRESSURE: 0}
STAY = (0, 32)  # (bank select: stays at the start with the program change on a flip)


def _frame(sh, rows):
    """(rides, u (each event's place along the box: 0 = first note, 1 = last end), b0, ub (beats where u = 0, beats
    per u: < 0 = flipped sideways)) or None."""
    r = rides_of(sh["cc"])
    if not r.events or not len(rows):
        return None
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    u = r.ticks / float(rows[:, 1].max())
    ub = b1 - b0
    if abs(b2 - b0) > 1e-9 or abs(p1 - p0) > 1e-9:  # (turned / slanted: over the box's whole time, its way)
        corners = [b0, b1, b2, b1 + b2 - b0]
        lo, hi = min(corners), max(corners)
        b0, ub = (lo, hi - lo) if ub >= 0 else (hi, lo - hi)
    return r, u, b0, ub


def _ticks(u, b0, ub, ppq):
    return np.maximum(np.rint((b0 + u * ub) * ppq), 0).astype(np.int64)


def placed(sh, rows, ppq):
    """The shape's passengers where its box is now: [(tick, track, tag, body)] in time order. rows = its unpacked
    notes (their last end = the box's length, as custom.block_notes). The box's time side carries them: moved /
    stretched along it; flipped sideways (that side runs back) = mirrored by the user's rule (mirrored); a turned
    or slanted box spreads them over the time it covers, the way its time side runs (user: turns leave them)."""
    got = _frame(sh, rows)
    if got is None:
        return []
    r, u, b0, ub = got
    if ub < 0:
        return mirrored(r, u, b0, ub, ppq)
    ticks = _ticks(u, b0, ub, ppq)
    order = np.argsort(ticks, kind="stable")  # (a tick's events keep their order)
    ev = r.events
    return [(t, ev[i][1], ev[i][2], ev[i][3]) for t, i in zip(ticks[order].tolist(), order.tolist())]


def spans(sh, ppq, rows=None):
    """{track: (first, last tick)} of the shape's passengers where it is now (render's busy, App cc_busy: each
    track's channel held while they're sent, hunt 2026-10-22)."""
    from notes.custom import unpack_notes
    got = _frame(sh, unpack_notes(sh["notes"]) if rows is None else rows)
    if got is None:
        return {}
    r, u, b0, ub = got
    ticks = _ticks(u, b0, ub, ppq)
    if ub < 0:  # (mirrored: the program change / last values go to its start too)
        ticks = np.append(ticks, _ticks(np.array([max(1.0, float(u.max()))]), b0, ub, ppq))
        tracks = np.append(r.tracks, -1)
    else:
        tracks = r.tracks
    out = {}
    for k in np.unique(r.tracks).tolist():
        mine = ticks[(tracks == k) | (tracks == -1)]
        out[k] = (int(mine.min()), int(mine.max()))
    return out


def shared_out(sh, parts, ppq):
    """A pasted shape cut into parts ((start, length, key, velocity, track) tick rows as they sound now, slice.py):
    each part's passengers packed (or None), ticks from its first note. By time per track (user 2026-10-22): an
    event goes to the part playing that track there (its latest note start at or before it), else the part where
    the track starts first; a track no part has: the first part."""
    from notes.custom import unpack_notes
    events = placed(sh, unpack_notes(sh["notes"]), ppq)
    if not events:
        return [None] * len(parts)
    ticks = np.fromiter((e[0] for e in events), np.int64, len(events))
    tracks = np.fromiter((e[1] for e in events), np.int64, len(events))
    to = np.zeros(len(events), np.int64)
    for k in np.unique(tracks).tolist():
        at = np.flatnonzero(tracks == k)
        best = np.full(len(at), -2 ** 62)  # (playing there: the latest start; else minus the first start)
        for p, n in enumerate(parts):
            s = np.sort(n[n[:, 4] == k, 0])
            if not len(s):
                continue
            i = np.searchsorted(s, ticks[at], "right")
            key = np.where(i > 0, 2 ** 40 + s[np.maximum(i - 1, 0)], -s[0])
            win = key > best
            best[win], to[at[win]] = key[win], p
    mine = [[] for _ in parts]
    for e, p in zip(events, to.tolist()):
        mine[p].append(e)
    return [pack_cc([(t - int(n[:, 0].min()), k, tag, b) for t, k, tag, b in got]) if got else None
            for got, n in zip(mine, parts)]


def track_map(notes, tracks, rows):
    """{track: slot}: which slot each track's notes have in rows ((start, end, pitch, velocity, slot, ...) rows of
    the same shape's notes, laid out however the copy was: Multi channel, as if alone, ...), found by start + key
    (hunt 2026-10-22: the main layout swapped two tracks' CCs on a merge copy); the slot most of them have."""
    notes, rows = np.asarray(notes, np.int64), np.asarray(rows, np.int64)
    tracks = np.asarray(tracks, np.int64)
    if not len(notes) or not len(rows):
        return {}
    have = notes[:, 0] * 1024 + notes[:, 2]
    o = np.argsort(have, kind="stable")
    have, tr = have[o], tracks[o]
    want = rows[:, 0] * 1024 + rows[:, 2]
    at = np.searchsorted(have, want, "right") - 1
    found = (at >= 0) & (have[np.maximum(at, 0)] == want)
    pairs = np.column_stack([tr[np.maximum(at, 0)][found], rows[found, 4]])
    out = {}
    if len(pairs):
        u, n = np.unique(pairs, axis=0, return_counts=True)
        for (k, s), c in sorted(zip(map(tuple, u.tolist()), n.tolist()), key=lambda e: e[1]):
            out[k] = s  # (the most often last)
    return out


def carried(shapes, slots_of, got, ppq):
    """Rendered rows (start, end, pitch, velocity, slot, owner) copied into a new notes shape whose tracks are their
    slots (Multi channel: Apply mask, Gate sensitive merge, Turn into plain notes): the passengers of the pasted
    shapes they came from (user: kept with their notes), each on the slot its track has in the copy (slots_of(o)
    -> {track: slot}, track_map), those inside the time the copy holds notes of that slot, at their own ticks.
    Packed, ticks from the copy's first note; None if none."""
    from notes.custom import unpack_notes
    got = np.asarray(got, np.int64)
    if not len(got):
        return None
    out, t0 = [], int(got[:, 0].min())
    for o in np.unique(got[:, 5]).tolist():
        sh = shapes[o]
        slots = slots_of(o) if "cc" in sh else None
        if not slots:
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


def mirrored(r, u, b0, ub, ppq):
    """placed for a box flipped sideways (user 2026-10-22): each control's values mirrored as STRETCHES (a value
    held from its event to the next one runs the other way, so it now starts where that stretch ends; the value
    held to the shape's end starts at its start; the value before the first event, the usual one, comes after
    where the first one was: only for controls with a known usual value); program changes and bank select stay at
    the start, in their order; anything else simply mirrored."""
    ev = r.events
    ticks = _ticks(u, b0, ub, ppq)
    start = int(_ticks(np.array([max(1.0, float(u.max()))]), b0, ub, ppq)[0])  # (the far end before the flip)
    stay = (r.tags == PROGRAM) | np.isin(r.ccm, STAY)
    ctrl = (r.ccm >= 0) & ~stay
    first = [(start, ev[i][1], ev[i][2], ev[i][3]) for i in np.flatnonzero(stay).tolist()]
    new = ticks.copy()
    k = np.flatnonzero(ctrl)
    if len(k):
        group = r.tracks[k] * 1024 + r.ccm[k]
        o = k[np.lexsort((u[k], group))]  # (each track's control in time order)
        g = r.tracks[o] * 1024 + r.ccm[o]
        last = np.append(g[1:] != g[:-1], True)
        new[o] = np.where(last, start, np.append(ticks[o][1:], 0))  # (its value from where its stretch ended)
        heads = o[np.append(True, g[1:] != g[:-1])]
    else:
        heads = []
    rest = np.flatnonzero(~stay)
    out = [(t, ev[i][1], ev[i][2], ev[i][3]) for t, i in zip(new[rest].tolist(), rest.tolist())]
    for i in list(heads):
        c = int(r.ccm[i])
        if c in USUAL:  # (before it: the usual value, from where the first one was)
            out.append((int(ticks[i]), ev[i][1], ev[i][2], with_value(ev[i][3], USUAL[c])))
    out.sort(key=lambda e: e[0])
    return first + out
