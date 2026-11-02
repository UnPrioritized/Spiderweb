"""Playback through a MIDI-OUT device: Windows' own (winmm.dll via ctypes, nothing to install) or the built-in synth
(BUILTIN, files/synth.py's Live; the only one elsewhere)."""

import bisect
import ctypes
import heapq
import os
import sys
import threading
import time

import numpy as np

from files.lang import tr
from files.system import WINDOWS, wintypes
from notes.engine import channels_of, ports_of
from notes.hz_settings import CC_NUMBER, CC_START

MAPPER_NAME = "Windows default (MIDI Mapper)"
BUILTIN = "Built-in BASSMIDI"  # (the user's name for it; like a device's name it's saved, so not translated)
KDMAPI = "OmniMIDI (KDMAPI)"  # (OmniMIDI's own direct way in, skipping Windows' MIDI layers; user's name, saved)
KESTREL = "Kestrel (GPU, Experimental)"  # (the GPU synth through files/kestrel.py; user 2026-10-27: its name may
# show; saved. "Experimental" user 2026-10-28: still rough on the heaviest MIDIs)
MAESTRO = "Maestro (API)"  # (the user's own install, through its API: files/maestro.py; user 2026-11-01; saved)
OLD_NAMES = {"Kestrel (GPU)": KESTREL}  # (saved MIDI outs under an earlier name)
DEFAULT_DEVICE = MAPPER_NAME if WINDOWS else BUILTIN
MAPPER = 0xFFFFFFFF

# 31-EDO sound (Built-in BASSMIDI only): row r = 31-EDO step r above C0. Each step of the octave has its own
# channel (EDO_FIRST + step), pitch-bent once when the synth opens (EDO_BENDS, at the usual +-2 key bend range),
# so any number of notes can sound at once and the note colours' channels don't matter. A message to channel 16
# or past carries its group of 16's number in its top byte (synth.Live.send). Channels 16-95 = ports 2-6's
# (a Hz bass's own channel per note: synth.LIVE_PORTS), so 31-EDO's come after them.
PACE_EVERY = 2048  # (Kestrel alone: messages in a burst between looks at "fell behind" / a new speed, Player._run)
STACKED = 512  # notes still sounding on one channel at Stop past which it gets one "all notes off" (Player.stop)
EDO = 31
EDO_FIRST = 96  # (synth.LIVE_PORTS x 16)
_EDO_KEYS = np.array([round(s * 1200 / EDO / 100) for s in range(EDO)], np.int64)  # nearest key above C, per step
EDO_BENDS = [8192 + round((s * 1200 / EDO - 100 * int(_EDO_KEYS[s])) / 100 * 4096) for s in range(EDO)]
# Kestrel (13g): 16 ports of its own and no Hz bass channels, so 31-EDO goes on the TOP two ports (15-16), or the
# highest two a loaded MIDI leaves free (hunt 2026-10-29: Lagtester's 8 ports shared 7-8 = drums + bends clashed)
EDO_KESTREL = 14 * 16


def edo_ports(used):
    """Kestrel's first 31-EDO channel: the highest two ports in a row a loaded MIDI doesn't use (`used`: its ports
    0..15), else the top two."""
    for p in range(14, -1, -1):
        if p not in used and p + 1 not in used:
            return p * 16
    return EDO_KESTREL


def edo_bends(center=False, first=EDO_FIRST):
    """The 31 pitch-bend messages setting up 31-EDO's channels from `first` (center: put back to no bend), packed for
    Live.send / KestrelOut.send (top byte = channel group)."""
    out = []
    for step, bend in enumerate(EDO_BENDS):
        ch, bend = first + step, 8192 if center else bend
        out.append(0xE0 | ch & 15 | (bend & 0x7F) << 8 | (bend >> 7) << 16 | (ch >> 4) << 24)
    return out


def edo_code(rows, first=EDO_FIRST):
    """channel | key << 8 (+ channel group << 24) of rows (a NumPy array or one int) in 31-EDO, channels from first."""
    rows = np.asarray(rows, np.int64)
    step = rows % EDO
    ch = first + step
    return (ch & 15) | (12 + 12 * (rows // EDO) + _EDO_KEYS[step]) << 8 | (ch >> 4) << 24

try:
    _winmm = ctypes.WinDLL("winmm")
except (AttributeError, OSError):
    _winmm = None


class _Caps(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32), ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]


if _winmm:
    _winmm.midiOutGetNumDevs.restype = wintypes.UINT
    _winmm.midiOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(_Caps), wintypes.UINT]
    _winmm.midiOutOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.c_size_t,
                                   ctypes.c_size_t, wintypes.DWORD]
    _winmm.midiOutShortMsg.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _winmm.midiOutReset.argtypes = [wintypes.HANDLE]
    _winmm.midiOutClose.argtypes = [wintypes.HANDLE]


def _winmm_devices():
    out = []
    for i in range(_winmm.midiOutGetNumDevs() if _winmm else 0):
        caps = _Caps()
        ok = _winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0
        out.append(caps.szPname if ok else tr("playback.device", i=i + 1))
    return out


def _kdmapi_path():
    """Where OmniMIDI's library is, or None. Windows: the system folder (a 32-bit Python is sent to SysWOW64 by
    Windows itself, so the bitness matches). Linux (OmniMIDI 2's libOmniMIDI.so, untested): the usual places."""
    if WINDOWS:
        path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OmniMIDI.dll")
        return path if os.path.isfile(path) else None
    if not _found:  # (the search starts other programs, ~0.1 s: done once, not at every dropdown opening)
        import ctypes.util
        _found.append(ctypes.util.find_library("OmniMIDI"))
    return _found[0]


_found = []  # (Linux: [the search's answer] once searched)
_kdmapi = None  # (OmniMIDI's library once loaded: loaded at the first open, after our BASS, see MidiOut.open)


def _load_kdmapi():
    """OmniMIDI's library with KDMAPI's calls set up, or raises OSError / AttributeError."""
    global _kdmapi
    if _kdmapi is None:
        path = _kdmapi_path()
        if not path:
            raise OSError("not found")
        lib = (ctypes.WinDLL if WINDOWS else ctypes.CDLL)(path)
        for f in ("IsKDMAPIAvailable", "InitializeKDMAPIStream", "TerminateKDMAPIStream"):
            getattr(lib, f).restype = ctypes.c_int
            getattr(lib, f).argtypes = []
        lib.SendDirectData.argtypes = [ctypes.c_uint32]
        lib.SendDirectData.restype = None
        lib.ResetKDMAPIStream.argtypes = []
        lib.ResetKDMAPIStream.restype = None
        _kdmapi = lib
    return _kdmapi


def devices():
    """Names of the MIDI-OUT devices: Windows' default first, then the built-in synths (Built-in BASSMIDI, Kestrel),
    then other programs' ways in when installed (OmniMIDI's KDMAPI, Maestro; user 2026-11-01), then Windows'
    devices."""
    from files import kestrel, maestro
    own = [BUILTIN] + ([KESTREL] if kestrel.available() is None else [])
    own += [KDMAPI] if _kdmapi_path() else []
    own += [MAESTRO] if maestro.lib_path() else []
    return [MAPPER_NAME] + own + _winmm_devices() if _winmm else own


def keep_saved(name):
    """A saved MIDI out to pick again: on Windows any (an unplugged device is told about at Play), elsewhere only
    what's there (a Windows device saved in a settings file brought over is dropped)."""
    return bool(_winmm) or OLD_NAMES.get(name, name) in devices()


class MidiOut:
    """One open MIDI-OUT device, kept open until another is picked. handle = winmm's, or the built-in synth's Live."""

    def __init__(self, make_live=None, make_kestrel=None):
        self.handle = None
        self.name = None
        self.make_live = make_live  # () -> a started synth.Live (or raises SynthError)
        self.make_kestrel = make_kestrel  # () -> a playing kestrel.KestrelOut (or raises SynthError)
        self.edo = False  # the project's notes played in 31-EDO (the app's 31-EDO + Audio ticks): see tuned
        self.edo_first = EDO_FIRST  # (its first channel on this out: Kestrel's moves, App.kestrel_parts)
        self.edo_bent = None  # (Kestrel: the first channel its 31-EDO bends were sent from, None = none bent)

    @property
    def tuned(self):
        """True when the piano roll's notes are played in 31-EDO now (Built-in BASSMIDI, Kestrel: 13g, user
        2026-10-29; Kestrel's channels get their bends at open and each Play, App.kestrel_parts)."""
        return self.edo and self.name in (BUILTIN, KESTREL)

    def open(self, name):
        """Open the device called name. Returns an error message, or None when it's ready."""
        if self.handle and self.name == name:
            return None
        self.close()
        err = self._open(name)
        from files import errors
        errors.note("MIDI out", name if err is None else f"{name} (couldn't open)")
        return err

    def bend_edo(self, first=None):
        """(Kestrel) 31-EDO's channels bent from `first` (None = keep edo_first), the ones bent before put back first
        when they were others; first False = only put back (not tuned now)."""
        if self.name != KESTREL or not self.handle:
            return
        new = self.edo_first if first is None else first
        if self.edo_bent is not None and self.edo_bent != new:
            for msg in edo_bends(center=True, first=self.edo_bent):
                self.handle.send(msg)
            self.edo_bent = None
        if new is False:
            return
        self.edo_first = new
        for msg in edo_bends(first=new):  # (sent again each time: a restart / reset may have put them back)
            self.handle.send(msg)
        self.edo_bent = new

    @property
    def lead(self):
        """Seconds early a player sends (send_at), 0 = at the time itself (send)."""
        return self.handle.lead if self.name in (KESTREL, MAESTRO) and self.handle else 0

    def send_at(self, msg, when, length=None):
        """(lead > 0) msg to sound at perf_counter time `when`; length = a note-on's in seconds."""
        self.handle.send_at(msg, when, length)

    def flush(self):
        """(lead > 0) what send_at queued, pushed in whole blocks."""
        if self.name == KESTREL and self.handle:
            self.handle.flush()

    def cancel(self):
        """(lead > 0) what was sent ahead forgotten (its note-offs sound now); Maestro: played first."""
        if self.name in (KESTREL, MAESTRO) and self.handle:
            self.handle.cancel()

    def _open(self, name):
        if name in (BUILTIN, KESTREL):
            from files.synth import SynthError
            try:
                self.handle = (self.make_live if name == BUILTIN else self.make_kestrel)()
            except SynthError as e:
                return str(e)
            self.name = name
            self.edo_first = EDO_KESTREL if name == KESTREL else EDO_FIRST
            self.edo_bent = None
            if name == KESTREL and self.edo:  # (listening before any Play: 31-EDO's channels bent already)
                self.bend_edo()
            return None
        if name == MAESTRO:
            return self._open_maestro()
        if name == KDMAPI:
            from files.synth import SynthError, load_dlls
            try:
                load_dlls()  # (ours first, as for the Windows devices below)
            except SynthError:
                pass
            try:
                lib = _load_kdmapi()
                ok = lib.IsKDMAPIAvailable() and lib.InitializeKDMAPIStream()
            except (OSError, AttributeError):
                ok = False
            if not ok:
                return tr("playback.kdmapi_failed")
            self.handle, self.name = lib, name
            return None
        if not _winmm:
            return tr("playback.midi_playback_only_works_on_windows")
        names = _winmm_devices()
        if name != MAPPER_NAME and name not in names:
            return tr("playback.the_midi_device_isn_t_there", name=name)
        dev = MAPPER if name == MAPPER_NAME else names.index(name)
        # The preview synth's BASS is loaded first: some MIDI-out devices run on BASS too and load theirs by name;
        # loaded before ours, the preview can't make sound (measured with one, 2026-10-01). Loaded after, they
        # share ours and both work.
        from files.synth import SynthError, load_dlls
        try:
            load_dlls()
        except SynthError:
            pass
        h = wintypes.HANDLE()
        err = _winmm.midiOutOpen(ctypes.byref(h), dev, 0, 0, 0)
        if err:
            return tr("playback.couldn_t_open_windows_error_another", name=name, err=err)
        self.handle, self.name = h, name
        return None

    def _open_maestro(self):
        from files import maestro
        from files.synth import SynthError, load_dlls
        try:
            load_dlls()  # (ours first: Maestro uses a BASS already loaded, ours, rather than a second copy)
        except SynthError:
            pass
        try:
            self.handle = maestro.MaestroOut()
        except maestro.MaestroError as e:
            if e.disabled:
                return tr("playback.maestro_disabled")
            return tr("playback.maestro_failed_why", why=str(e) or "?")
        except (OSError, AttributeError):
            return tr("playback.maestro_failed")
        self.name = MAESTRO
        return None

    def send(self, msg):
        h = self.handle
        if h:
            if self.name in (BUILTIN, KESTREL, MAESTRO):
                h.send(msg)
            elif self.name == KDMAPI:
                h.SendDirectData(msg)
            else:
                _winmm.midiOutShortMsg(h, msg)

    def note(self, ch, pitch, vel, rows=False):
        """Note on (vel 0 = note off). Keys above 127 (256 keys) can't be sent: skipped. rows = pitch is a piano
        roll row: played in 31-EDO while tuned (every row, ch not used)."""
        if rows and self.tuned:
            self.send(0x90 | int(edo_code(pitch, self.edo_first)) | vel << 16)
        elif pitch <= 127:
            self.send(0x90 | ch | pitch << 8 | vel << 16)

    def close(self):
        if self.handle:
            if self.name in (BUILTIN, KESTREL, MAESTRO):
                self.handle.close()
            elif self.name == KDMAPI:
                self.handle.ResetKDMAPIStream()
                self.handle.TerminateKDMAPIStream()
            else:
                _winmm.midiOutReset(self.handle)
                _winmm.midiOutClose(self.handle)
        self.handle = self.name = None


class _Ons:
    """The notes to play, in the order they start: only the order is worked out (the notes stay where they are, so
    millions take little memory), and they're turned into Python numbers a batch at a time as they're played."""

    def __init__(self, notes, t0, t1, use10, tune=False, ports=0, edo_first=EDO_FIRST):
        s = notes[:, 0]
        if tune:  # (31-EDO: every row fits in the synth's 128 keys)
            keep = np.flatnonzero((s >= t0) & (s < t1))
        else:
            keep = np.flatnonzero((s >= t0) & (s < t1) & (notes[:, 2] <= 127))  # (a synth has 128 keys)
        starts = s[keep]
        if len(starts) > 1 and not (starts[1:] >= starts[:-1]).all():
            order = np.argsort(starts, kind="stable")
            keep, starts = keep[order], starts[order]
            del order
        self.notes, self.keep, self.starts, self.use10, self.tune = notes, keep, starts, use10, tune
        self.edo_first = edo_first  # (31-EDO's first channel on this out: MidiOut.edo_first)
        self.ports = ports  # (the synth's ports, 0 = none: each slot on its port's channels as the MIDI
        # export writes them, engine.slot_ports, round again past the last)

    def __len__(self):
        return len(self.keep)

    def first_after(self, tick):
        """Index of the first note starting after tick."""
        return int(np.searchsorted(self.starts, tick, "right"))

    def batch(self, i, size=4096):
        """Notes i.. as (start, end, channel | key << 8, velocity) tuples."""
        return self._tuples(self.notes[self.keep[i:i + size]])

    def sounding(self, tick):
        """{channel | key << 8: [ends, sorted]} of the notes started at / before tick still sounding after it."""
        idx = self.keep[:self.first_after(tick)]
        idx = idx[self.notes[idx, 1] > tick]
        got = {}
        for s, e, key, vel in self._tuples(self.notes[idx]):
            got.setdefault(key, []).append(e)
        for ends in got.values():
            ends.sort()
        return got

    def _tuples(self, part):
        if self.tune:
            return list(zip(part[:, 0].tolist(), part[:, 1].tolist(), edo_code(part[:, 2], self.edo_first).tolist(),
                            part[:, 3].tolist()))
        slot = part[:, 4]
        ch = channels_of(slot, self.use10)
        if self.ports:
            ch = ch | ports_of(slot, self.use10, self.ports) << 24
        return list(zip(part[:, 0].tolist(), part[:, 1].tolist(), (ch | part[:, 2] << 8).tolist(),
                        part[:, 3].tolist()))


RPN = (101, 100, 6, 38)  # (the CCs that set the bend range: sent in their order, never thinned out)
# what a Hz bass's CC lines are put back to on a channel when playing stops (hz_settings.CC_START: where players
# start them, user: nothing left on the channel), the bend range back to 2 keys
CC_RESET = [(CC_NUMBER[n], CC_START[n]) for n in CC_NUMBER]
RANGE_RESET = [(101, 0), (100, 0), (6, 2), (38, 0), (101, 127), (100, 127)]


def cc_list(messages, use10, ports=0):
    """{slot: [(tick, status, data 1, data 2)]} (hz_cc.slot_events) -> [(tick, message)] in time order, each a short
    MIDI message on its slot's channel (as _Ons.batch / slot_track_channel; ports = the synth's ports: + its port
    << 24)."""
    out = []
    for slot, events in messages.items():
        ch = int(channels_of(slot, use10))
        if ports:  # (the port its notes are on: _Ons.batch)
            ch |= int(ports_of(slot, use10, ports)) << 24
        out += [(e[0], e[1] | ch | e[2] << 8 | e[3] << 16) for e in events
                if e[1] is not None]  # (a pasted Domino event without a MIDI message: none)
    out.sort(key=lambda e: e[0])
    return out


def cc_before(ccs, tick):
    """The messages of ccs (cc_list) before tick a player starting there sends first: each CC's and the pitch
    bend's last value on each channel (the bend range's messages all, in order)."""
    last, out = {}, []
    for t, m in ccs:
        if t >= tick:
            break
        kind = m & 0xF0
        if kind == 0xB0 and (m >> 8) & 0x7F in RPN:
            out.append(m)
        else:
            last[(m & 0x7FFF if kind == 0xB0 else m & 0xFF) | m & 0xFF000000] = m  # (its port too)
    return out + list(last.values())


def _cc_kind(m):
    """(channel, CC number / "bend") of a message cc_list made, for Player.stop's putting back; None: one of the
    bend range's (put back with the pitch bend)."""
    ch = m & 0xFF00000F  # (with its port)
    if m & 0xF0 == 0xE0:
        return ch, "bend"
    n = (m >> 8) & 0x7F
    return (ch, n) if n in dict(CC_RESET) else (ch, None)


def cc_reset(kinds):
    """The messages putting (channel, CC number / "bend") kinds (_cc_kind's) back to where players start them."""
    out = []
    for ch, kind in sorted(kinds, key=str):
        if kind == "bend":
            out.append(0xE0 | ch | 64 << 16)
            out += [0xB0 | ch | n << 8 | v << 16 for n, v in RANGE_RESET]
        elif kind is not None:
            out.append(0xB0 | ch | kind << 8 | dict(CC_RESET)[kind] << 16)
    return out


class TimeMap:
    """Seconds into the song at each tick, from tempo changes [(tick, microseconds a beat)] (tick 0 first): one BPM
    is one change; the Tempo pane's line = tempo_file.heard_tempo."""

    def __init__(self, ppq, changes):
        self.ticks = [t for t, _ in changes]
        self.spt = [u / 1e6 / ppq for _, u in changes]  # seconds a tick
        self.secs = [0.0]
        for i in range(1, len(changes)):
            self.secs.append(self.secs[-1] + (self.ticks[i] - self.ticks[i - 1]) * self.spt[i - 1])

    def sec(self, tick):
        i = max(0, bisect.bisect_right(self.ticks, tick) - 1)
        return self.secs[i] + (tick - self.ticks[i]) * self.spt[i]

    def tick(self, sec):
        i = max(0, bisect.bisect_right(self.secs, sec) - 1)
        return self.ticks[i] + (sec - self.secs[i]) / self.spt[i]


class Player:
    """Plays notes on a background thread so the window stays responsive; position() says where it is. The notes
    and tempo can change while it plays (update): it plays on with the new ones from where it is."""

    def __init__(self, out):
        self.out = out
        self.thread = None
        self._stop = threading.Event()
        self._switch = sys.getswitchinterval()
        self.held = {}  # (channel | key << 8) -> how many notes on it are sounding right now
        self.cc_sent = set()  # (channel, CC number or "bend") a Hz bass's CC lines sent a value on: put back at stop
        self.tune = False  # (31-EDO: see start)
        self.edo_first = EDO_FIRST
        self.shared = False  # (set before start: overlapping notes may share a channel, a key sounds until its last)
        self.along = False  # (set before start: a loaded MIDI plays along, its feeder re-times Kestrel's pushes)
        self.ports = False  # (see start)
        self._clock = None
        self.map = None  # the tempo as a TimeMap (start; None until the first start), slowed down by speed
        self.speed = 1.0  # (13f: under 1 = the song plays that much slower, every tempo's beat longer; set_speed)
        self._speed_wanted = None  # (Kestrel alone: a speed the playing thread takes up, _take_speed)
        self._tempo = (120, None)  # (bpm, tempo) as handed over, before the speed
        self._swap = None  # new notes ready for the playing thread (_Ons)
        self._wanted = None  # new notes waiting to be put in order (notes, use10)
        self._sorting = False  # a thread is putting them in order (cleared by it under _lock as it ends)
        self._lock = threading.Lock()
        self._clock_lock = threading.Lock()  # (_kestrel_wait vs the window's tempo / note changes moving the clock)
        self._gen = 0  # one for each start (a sort left from an earlier play is thrown away)
        self.ready = None  # a threading.Event the line waits for before it starts (play_loaded.LoadedPlayer.ready)
        self.notes_ver = 0  # (+1 each time other notes are handed over: Kestrel's remembered note limits start over)
        self._notes_of = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, notes, ppq, bpm, start_beat, stop_beat, use10=False, cc=(), ports=0, tempo=None):
        """Play from start_beat until stop_beat (notes: rendered [start, end, pitch, vel, slot, ...] in ticks, never
        changed in place; use10: the slots use channel 10 too, engine.slot_track_channel; cc = other MIDI messages,
        cc_list: the values before start_beat are sent first; ports: the synth's ports, each slot on its port's
        channels (engine.slot_ports; Built-in BASSMIDI only, synth.LIVE_PORTS). 31-EDO: no cc (its channels aren't
        the notes'). tempo = [(tick, us)] tempo changes instead of the one bpm (the Tempo pane's)."""
        self.ports = 0 if self.out.tuned else ports
        self.stop()
        self._new_notes(notes)
        self.ppq, self.start_beat, self.stop_beat = ppq, start_beat, stop_beat
        self.tune = self.out.tuned  # (31-EDO: kept until the next start, so note-offs match their note-ons)
        self.edo_first = getattr(self.out, "edo_first", EDO_FIRST)  # (outs without 31-EDO: the usual)
        self.speed, self._speed_wanted = 1.0, None
        self.map = self._time_map(bpm, tempo)
        self._clock = None  # (the line waits at the start while the notes are put in order)
        self._gen += 1
        self._swap = self._wanted = None
        self._stop.clear()
        sys.setswitchinterval(0.001)  # hand the thread the processor quickly while the window redraws
        self.thread = threading.Thread(target=self._run, args=(notes, use10, () if self.tune else cc), daemon=True)
        self.thread.start()
        from files import errors
        errors.note("playing", "yes")  # (the next start's crash message: was it playing?)

    def update(self, notes, bpm, stop_beat, use10=False, cc=(), ports=0, tempo=None):
        """The notes / tempo changed while playing (same PPQ): play on with them. Notes already sounding end where
        they end NOW (deleted = at once, shortened = at the new end; hunt 2026-10-28); new notes starting before the
        line aren't played (like a start from the line)."""
        if not self.running:
            return
        self._new_notes(notes)
        self.ports = 0 if self.tune else ports
        self.stop_beat = stop_beat
        new = self._time_map(bpm, tempo)
        with self._clock_lock:
            clock = self._clock
            if clock is not None:  # (the same spot in the song now, at the new speed)
                now = time.perf_counter()
                t, sec, old = clock
                self._clock = (now, new.sec(old.tick(sec + now - t)), new)
        self.map = new
        with self._lock:  # (a thread just ending would miss these notes: it says it's done under the same lock)
            self._wanted = (self._gen, notes, use10, () if self.tune else cc)
            if not self._sorting:
                self._sorting = True
                threading.Thread(target=self._sort, daemon=True).start()

    def _new_notes(self, notes):
        """notes_ver goes up when these notes aren't the ones handed over last (the same array = unchanged)."""
        if notes is not self._notes_of:
            self._notes_of = notes
            self.notes_ver += 1

    def _time_map(self, bpm, tempo):
        """The TimeMap played: the tempo handed over (kept for set_speed), each beat 1 / speed as long."""
        self._tempo = (bpm, tempo)
        v = self.speed
        return TimeMap(self.ppq, [(t, u / v) for t, u in tempo or [(0, 60_000_000 / bpm)]])

    def set_speed(self, v):
        """13f: play on from the same spot at v times the song's speed (1 = as written), the pitch unchanged. Goes
        the way of a tempo change while playing (Kestrel: what was sent ahead sent again at the new speed). Kestrel
        playing own shapes alone: done by the playing thread (hunt 2026-10-29: Kestrel's take-back waits for its next
        block, longest while overloaded, and the window waited with it)."""
        if v == (self._speed_wanted if self._speed_wanted is not None else self.speed):
            return
        if self.running and getattr(self.out, "name", None) == KESTREL and not self.along:
            self._speed_wanted = v
            return
        self._speed_wanted = None
        self.speed = v
        if self.running:
            self.set_tempo(*self._tempo)

    def _take_speed(self):
        """(the playing thread) A speed asked for by set_speed, now."""
        v, self._speed_wanted = self._speed_wanted, None
        if v is not None and v != self.speed:
            self.speed = v
            self.set_tempo(*self._tempo)

    def set_tempo(self, bpm, tempo=None):
        """Only the tempo changed while playing: on at the new speed from the same spot, the notes untouched."""
        if not self.running:
            return
        new = self._time_map(bpm, tempo)
        with self._clock_lock:
            clock = self._clock
            if clock is not None:
                now = time.perf_counter()
                t, sec, old = clock
                k = self.out.handle if getattr(self.out, "name", None) == KESTREL and not self.along else None
                # (alone: the loaded MIDI's feeder does this when one plays along, play_loaded._retime)
                if k is None or not self._kestrel_retime(k, clock, new):
                    self._clock = (now, new.sec(old.tick(sec + now - t)), new)
        self.map = new

    def kestrel_wait(self, k):
        """Kestrel fell behind (handed out silence while it caught up, so all later sound comes later): the play line
        waits as long, so it stays with the sound (13e, always on; Built-in BASSMIDI never falls behind). k = the
        KestrelOut; called by the thread that sends to it (the loaded MIDI's feeder when one plays along)."""
        with self._clock_lock:
            d = k.waited()
            clock = self._clock
            if d and clock is not None:
                self._clock = (clock[0] + d, clock[1], clock[2])

    def _kestrel_retime(self, k, clock, new):
        """Kestrel (own shapes alone): what was sent ahead at the old tempo taken back from a couple of blocks on
        and sent again at the new one; the new tempo starts exactly where the sound made at the old one ends, the
        clock set to match (as play_loaded._retime; hunt 2026-10-28: ~0.2 s at the old speed, a note rang on)."""
        from files.kestrel import RATE
        B = k.k.block
        at = k.k.cut((k.k.stats()["rendered"] // B + 2) * B)
        if at is None:  # (no answer: on as before, the clock alone)
            return False
        t, sec, old = clock
        base = k._f0 + (t - sec - k._t0) * RATE  # (frame of song second 0 at the old tempo)
        anchor = at - new.sec(old.tick((at - base) / RATE)) * RATE
        k.retime(at, lambda f: round(anchor + new.sec(old.tick((f - base) / RATE)) * RATE))
        self._clock = (k._t0 + (anchor - k._f0) / RATE, 0.0, new)
        return True

    def _sort(self):
        while True:
            with self._lock:
                if self._wanted is None:
                    self._sorting = False
                    return
                (gen, notes, use10, cc), self._wanted = self._wanted, None
            ons = _Ons(notes, self.start_beat * self.ppq, self.stop_beat * self.ppq, use10, self.tune, self.ports,
                       self.edo_first)
            if self._wanted is None and gen == self._gen:  # (changed again meanwhile: those first)
                self._swap = (ons, cc)

    def position(self):
        """Current beat."""
        clock = self._clock
        if clock is None:
            return self.start_beat
        t, sec, tmap = clock
        return min(self.stop_beat, tmap.tick(sec + max(0.0, time.perf_counter() - t)) / self.ppq)

    def _run(self, notes, use10, ccs=()):
        clock, sleep, stop = time.perf_counter, time.sleep, self._stop
        out = self.out
        send = out.send
        # (Kestrel: each message sent `lead` seconds early, stamped with when it sounds: exact times, whole blocks)
        lead = getattr(out, "lead", 0)
        if lead:
            emit, flush = out.send_at, out.flush
        else:
            def emit(m, when, length=None):
                send(m)

            def flush():
                pass
        t0 = self.start_beat * self.ppq
        tune = self.tune
        # (Kestrel ends one note per note-off, oldest first: every note gets its own, else the later one rang on, hunt
        # 2026-10-28; BASSMIDI ends every note of the key at one)
        shared = (tune or self.shared) and getattr(out, "name", None) != KESTREL
        ons = _Ons(notes, t0, self.stop_beat * self.ppq, use10, tune, self.ports, self.edo_first)
        del notes
        ready = self.ready  # (a loaded MIDI playing along: the line waits until its first notes are read)
        while ready is not None and not ready.wait(0.01):
            if stop.is_set():
                return
        # (Maestro sends ahead too, with nothing of Kestrel's; no handle: nothing open, like MidiOut's own checks)
        kestrel = getattr(out, "name", None) == KESTREL and getattr(out, "handle", None) is not None
        if kestrel:
            out.handle.resync()
        # (Kestrel falling behind = the line waits: here when alone, else the loaded MIDI's feeder does it)
        behind = out.handle if kestrel and not self.along else None
        # (Kestrel: the line starts `lead` from now, so the first notes are sent ahead too)
        self._clock = (clock() + lead, self.map.sec(t0), self.map)
        held = self.held = {}
        sent = self.cc_sent = set()
        for m in cc_before(ccs, t0):  # (the CC lines' values where it starts)
            emit(m, self._clock[0])
            sent.add(_cc_kind(m))
        j = next((n for n, (t, _) in enumerate(ccs) if t >= t0), len(ccs))
        offs = []  # (end tick, channel | key << 8) of the notes sounding
        batch, k, i = [], 0, 0
        done = t0 - 1  # the last tick played
        ons_at = [done, 0]  # (the tick of the last note-on handled, how many on it so far)
        def pace():
            """Kestrel alone: the line waits where it fell behind (13e); a speed asked for is taken up (13f)."""
            self.kestrel_wait(behind)
            if self._speed_wanted is not None:
                self._take_speed()
        # (asked when about to sleep, and every PACE_EVERY messages in a burst that never sleeps: asked at every
        # message it cost dense own shapes most of their notes, hunt 2026-10-29)
        busy = 0
        while True:
            if stop.is_set():
                return
            if behind is not None:
                busy += 1
                if busy >= PACE_EVERY:
                    busy = 0
                    pace()
            if self._swap is not None:
                (ons, ccs), self._swap = self._swap, None
                # (on from the notes of the tick it's on that weren't sent yet, not the next tick: a big chord
                # mid-way keeps its other notes)
                i = ons.first_after(done)
                if ons_at[0] == done:
                    i = min(i, ons.first_after(done - 1) + ons_at[1])
                batch, k = [], 0
                # (notes sounding now: let go where they end NOW, a deleted one at once, a shortened one at its new
                # end; they rang on to their old ends: "what you see is what you hear", hunt 2026-10-28)
                if held:
                    now_ends = ons.sounding(done)
                    t_now, sec_now, tmap = self._clock
                    when = t_now + tmap.sec(done) - sec_now
                    offs.clear()
                    for key, n in list(held.items()):
                        ends = now_ends.get(key, [])[:n]
                        gone = n - len(ends)
                        if gone and (not shared or not ends):
                            for _ in range(1 if shared else gone):
                                emit(0x80 | key, when)
                        if ends:
                            held[key] = len(ends)
                            offs.extend((e, key) for e in ends)
                        else:
                            del held[key]
                    heapq.heapify(offs)
                j = next((n for n, (t, _) in enumerate(ccs) if t > done), len(ccs))
                # (the CC lines changed: each channel's values where it is now, heard at once; channels the lines
                # no longer use put back, so nothing is left on them for what plays there)
                now = cc_before(ccs, done + 1)
                kinds = {_cc_kind(m) for _, m in ccs}
                kinds |= {(ch, None) for ch, kind in kinds if kind == "bend"}
                t_now, sec_now, tmap = self._clock
                when = t_now + tmap.sec(done) - sec_now
                for m in cc_reset({kind for kind in sent - kinds if kind[1] is not None}):
                    emit(m, when)
                sent &= kinds
                for m in now:
                    emit(m, when)
                    sent.add(_cc_kind(m))
            if k == len(batch) and i < len(ons):
                batch, k = ons.batch(i), 0
                i += len(batch)
            on = batch[k] if k < len(batch) else None
            c = ccs[j] if j < len(ccs) else None
            # (on the same tick: note-offs, then the other messages, then note-ons)
            off = bool(offs) and (on is None or offs[0][0] <= on[0]) and (c is None or offs[0][0] <= c[0])
            other = not off and c is not None and (on is None or c[0] <= on[0])
            tick = offs[0][0] if off else c[0] if other else on[0] if on is not None else None
            t_now, sec_now, tmap = self._clock
            t1 = self.stop_beat * self.ppq
            if tick is None or tick >= t1:  # nothing more to play: wait for the end
                wait = t_now + tmap.sec(t1) - sec_now - clock()
                if wait <= 0:
                    return
                flush()
                if behind is not None:
                    pace()
                sleep(min(wait, 0.01))
                continue
            when = t_now + tmap.sec(tick) - sec_now
            wait = when - clock()
            if wait > lead:
                flush()
                if behind is not None:
                    pace()
                sleep(min(wait - lead, 0.01))
                continue  # (stopped, new notes or a new tempo meanwhile: looked at again)
            done = tick
            if other:
                emit(c[1], when)
                sent.add(_cc_kind(c[1]))
                j += 1
                continue
            if off:
                key = heapq.heappop(offs)[1]
                n = held.get(key, 0) - 1
                # (31-EDO: every colour's notes on a row share its one channel, so the row sounds until its last
                # note ends, like the colours' own channels do in 12-tone; shapes keeping their colours the same)
                if n <= 0 or not shared:
                    emit(0x80 | key, when)
                if n > 0:
                    held[key] = n
                else:
                    held.pop(key, None)
                continue
            k += 1
            ons_at = [tick, ons_at[1] + 1] if ons_at[0] == tick else [tick, 1]
            if wait < -0.1:
                continue  # far behind (too many notes at once): skip late notes instead of smearing them
            s, e, key, vel = on
            if shared and held.get(key):
                # (the key sounding already: let go first, ONE sound a key. Every on stacked a voice while the off
                # waited for the last note: rows never quiet piled ~49 k voices and playback froze, 2026-10-31)
                emit(0x80 | key, when)
            # (its length: Kestrel's note budget keeps the longest start of a key, a held note beside spam)
            emit(0x90 | key | vel << 16, when, tmap.sec(e) - tmap.sec(s) if lead else None)
            held[key] = held.get(key, 0) + 1
            heapq.heappush(offs, (e, key))

    def stop(self):
        if self.thread is not None:
            self._stop.set()
            self.thread.join()
            self.thread = None
            getattr(self.out, "cancel", lambda: None)()  # (Kestrel: what was sent ahead goes)
            # ordinary note-offs for what's still sounding, so the synth plays its release (a reset cuts it off);
            # one for each note on a key (overlapping notes there each got a note-on). A channel with very many
            # (Sustain's stacked notes: sustain.py) gets one "all notes off" instead, also a release: one by one, a
            # million held notes kept Stop waiting
            per = {}
            for key, n in self.held.items():
                ch = key & ~0xFFFF00  # (the channel, with its group in the top byte)
                per[ch] = per.get(ch, 0) + n
            # (channels past 16 take note on / off only: synth.Live.send)
            many = {ch for ch, n in per.items() if n > STACKED and ch < 1 << 24}
            for key, n in self.held.items():
                if key & ~0xFFFF00 not in many:
                    for _ in range(n):
                        self.out.send(0x80 | key)
            for ch in many:
                self.out.send(0xB0 | ch | 123 << 8)
            # (a Hz bass's CC lines: every channel they sent a value on gets its start values back, user)
            for m in cc_reset(self.cc_sent):
                self.out.send(m)
            self.cc_sent = set()
            self.held = {}
            self._swap = self._wanted = None
            sys.setswitchinterval(self._switch)
            from files import errors
            errors.note("playing", "no")
