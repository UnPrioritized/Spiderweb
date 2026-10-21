"""Playback through a MIDI-OUT device: Windows' own (winmm.dll via ctypes, nothing to install) or the built-in synth
(BUILTIN, files/synth.py's Live; the only one elsewhere)."""

import ctypes
import heapq
import os
import sys
import threading
import time

import numpy as np

from files.lang import tr
from files.system import WINDOWS, wintypes
from notes.engine import CHANNELS
from notes.hz_settings import CC_NUMBER, CC_START

MAPPER_NAME = "Windows default (MIDI Mapper)"
BUILTIN = "Built-in BASSMIDI"  # (the user's name for it; like a device's name it's saved, so not translated)
KDMAPI = "OmniMIDI (KDMAPI)"  # (OmniMIDI's own direct way in, skipping Windows' MIDI layers; user's name, saved)
DEFAULT_DEVICE = MAPPER_NAME if WINDOWS else BUILTIN
MAPPER = 0xFFFFFFFF

# 31-EDO sound (Built-in BASSMIDI only): row r = 31-EDO step r above C0. Each step of the octave has its own
# channel (EDO_FIRST + step), pitch-bent once when the synth opens (EDO_BENDS, at the usual +-2 key bend range),
# so any number of notes can sound at once and the note colours' channels don't matter. A message to channel 16
# or past carries its group of 16's number in its top byte (synth.Live.send). Channels 16-95 = ports 2-6's
# (a Hz bass's own channel per note: synth.LIVE_PORTS), so 31-EDO's come after them.
STACKED = 512  # notes still sounding on one channel at Stop past which it gets one "all notes off" (Player.stop)
EDO = 31
EDO_FIRST = 96  # (synth.LIVE_PORTS x 16)
_EDO_KEYS = np.array([round(s * 1200 / EDO / 100) for s in range(EDO)], np.int64)  # nearest key above C, per step
EDO_BENDS = [8192 + round((s * 1200 / EDO - 100 * int(_EDO_KEYS[s])) / 100 * 4096) for s in range(EDO)]


def edo_code(rows):
    """channel | key << 8 (+ channel group << 24) of rows (a NumPy array or one int) in 31-EDO."""
    rows = np.asarray(rows, np.int64)
    step = rows % EDO
    ch = EDO_FIRST + step
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
    """Names of the MIDI-OUT devices: Windows' default first, then the built-in synth, OmniMIDI's direct way when
    it's installed, then Windows' devices."""
    kd = [KDMAPI] if _kdmapi_path() else []
    return [MAPPER_NAME, BUILTIN] + kd + _winmm_devices() if _winmm else [BUILTIN] + kd


def keep_saved(name):
    """A saved MIDI out to pick again: on Windows any (an unplugged device is told about at Play), elsewhere only
    what's there (a Windows device saved in a settings file brought over is dropped)."""
    return bool(_winmm) or name in devices()


class MidiOut:
    """One open MIDI-OUT device, kept open until another is picked. handle = winmm's, or the built-in synth's Live."""

    def __init__(self, make_live=None):
        self.handle = None
        self.name = None
        self.make_live = make_live  # () -> a started synth.Live (or raises SynthError)
        self.edo = False  # the project's notes played in 31-EDO (the app's 31-EDO + Audio ticks): see tuned

    @property
    def tuned(self):
        """True when the piano roll's notes are played in 31-EDO now (Built-in BASSMIDI only)."""
        return self.edo and self.name == BUILTIN

    def open(self, name):
        """Open the device called name. Returns an error message, or None when it's ready."""
        if self.handle and self.name == name:
            return None
        self.close()
        err = self._open(name)
        from files import errors
        errors.note("MIDI out", name if err is None else f"{name} (couldn't open)")
        return err

    def _open(self, name):
        if name == BUILTIN:
            from files.synth import SynthError
            try:
                self.handle = self.make_live()
            except SynthError as e:
                return str(e)
            self.name = name
            return None
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

    def send(self, msg):
        h = self.handle
        if h:
            if self.name == BUILTIN:
                h.send(msg)
            elif self.name == KDMAPI:
                h.SendDirectData(msg)
            else:
                _winmm.midiOutShortMsg(h, msg)

    def note(self, ch, pitch, vel, rows=False):
        """Note on (vel 0 = note off). Keys above 127 (256 keys) can't be sent: skipped. rows = pitch is a piano
        roll row: played in 31-EDO while tuned (every row, ch not used)."""
        if rows and self.tuned:
            self.send(0x90 | int(edo_code(pitch)) | vel << 16)
        elif pitch <= 127:
            self.send(0x90 | ch | pitch << 8 | vel << 16)

    def close(self):
        if self.handle:
            if self.name == BUILTIN:
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

    def __init__(self, notes, t0, t1, use10, tune=False, ports=0):
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
        self.ports = ports  # (the synth's ports, 0 = none: each slot on its port's channels as the MIDI
        # export writes them, engine.slot_ports, round again past the last)

    def __len__(self):
        return len(self.keep)

    def first_after(self, tick):
        """Index of the first note starting after tick."""
        return int(np.searchsorted(self.starts, tick, "right"))

    def batch(self, i, size=4096):
        """Notes i.. as (start, end, channel | key << 8, velocity) tuples."""
        part = self.notes[self.keep[i:i + size]]
        if self.tune:
            return list(zip(part[:, 0].tolist(), part[:, 1].tolist(), edo_code(part[:, 2]).tolist(),
                            part[:, 3].tolist()))
        slot = part[:, 4]
        ch = slot % 16 if self.use10 else np.array(CHANNELS, np.int64)[slot % len(CHANNELS)]  # (slot_track_channel)
        if self.ports:
            ch = ch | slot // (16 if self.use10 else len(CHANNELS)) % self.ports << 24  # (slot_port)
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
        ch = slot % 16 if use10 else CHANNELS[slot % len(CHANNELS)]
        if ports:  # (the port its notes are on: _Ons.batch)
            ch |= slot // (16 if use10 else len(CHANNELS)) % ports << 24
        out += [(t, s | ch | a << 8 | b << 16) for t, s, a, b in events]
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
        self.shared = False  # (set before start: overlapping notes may share a channel, a key sounds until its last)
        self.ports = False  # (see start)
        self._clock = None
        self._swap = None  # new notes ready for the playing thread (_Ons)
        self._wanted = None  # new notes waiting to be put in order (notes, use10)
        self._sorting = False  # a thread is putting them in order (cleared by it under _lock as it ends)
        self._lock = threading.Lock()
        self._gen = 0  # one for each start (a sort left from an earlier play is thrown away)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, notes, ppq, bpm, start_beat, stop_beat, use10=False, cc=(), ports=0):
        """Play from start_beat until stop_beat (notes: rendered [start, end, pitch, vel, slot, ...] in ticks, never
        changed in place; use10: the slots use channel 10 too, engine.slot_track_channel; cc = other MIDI messages,
        cc_list: the values before start_beat are sent first; ports: the synth's ports, each slot on its port's
        channels (engine.slot_ports; Built-in BASSMIDI only, synth.LIVE_PORTS). 31-EDO: no cc (its channels aren't
        the notes')."""
        self.ports = 0 if self.out.tuned else ports
        self.stop()
        self.ppq, self.start_beat, self.stop_beat = ppq, start_beat, stop_beat
        self.tune = self.out.tuned  # (31-EDO: kept until the next start, so note-offs match their note-ons)
        self.spt = 60 / bpm / ppq  # seconds a tick
        self._clock = None  # (the line waits at the start while the notes are put in order)
        self._gen += 1
        self._swap = self._wanted = None
        self._stop.clear()
        sys.setswitchinterval(0.001)  # hand the thread the processor quickly while the window redraws
        self.thread = threading.Thread(target=self._run, args=(notes, use10, () if self.tune else cc), daemon=True)
        self.thread.start()

    def update(self, notes, bpm, stop_beat, use10=False, cc=(), ports=0):
        """The notes / tempo changed while playing (same PPQ): play on with them. Notes already sounding end where
        they would have; new notes starting before the line aren't played (like a start from the line)."""
        if not self.running:
            return
        self.ports = 0 if self.tune else ports
        self.stop_beat = stop_beat
        spt = 60 / bpm / self.ppq
        clock = self._clock
        if spt != self.spt and clock is not None:  # (the same spot in the song now, at the new speed)
            now = time.perf_counter()
            self._clock = (now, clock[1] + (now - clock[0]) / clock[2], spt)
        self.spt = spt
        with self._lock:  # (a thread just ending would miss these notes: it says it's done under the same lock)
            self._wanted = (self._gen, notes, use10, () if self.tune else cc)
            if not self._sorting:
                self._sorting = True
                threading.Thread(target=self._sort, daemon=True).start()

    def _sort(self):
        while True:
            with self._lock:
                if self._wanted is None:
                    self._sorting = False
                    return
                (gen, notes, use10, cc), self._wanted = self._wanted, None
            ons = _Ons(notes, self.start_beat * self.ppq, self.stop_beat * self.ppq, use10, self.tune, self.ports)
            if self._wanted is None and gen == self._gen:  # (changed again meanwhile: those first)
                self._swap = (ons, cc)

    def position(self):
        """Current beat."""
        clock = self._clock
        if clock is None:
            return self.start_beat
        t, tick, spt = clock
        return min(self.stop_beat, (tick + (time.perf_counter() - t) / spt) / self.ppq)

    def _run(self, notes, use10, ccs=()):
        clock, sleep, stop = time.perf_counter, time.sleep, self._stop
        send = self.out.send
        t0 = self.start_beat * self.ppq
        tune = self.tune
        shared = tune or self.shared
        ons = _Ons(notes, t0, self.stop_beat * self.ppq, use10, tune, self.ports)
        del notes
        self._clock = (clock(), t0, self.spt)
        held = self.held = {}
        sent = self.cc_sent = set()
        for m in cc_before(ccs, t0):  # (the CC lines' values where it starts)
            send(m)
            sent.add(_cc_kind(m))
        j = next((n for n, (t, _) in enumerate(ccs) if t >= t0), len(ccs))
        offs = []  # (end tick, channel | key << 8) of the notes sounding
        batch, k, i = [], 0, 0
        done = t0 - 1  # the last tick played
        while True:
            if stop.is_set():
                return
            if self._swap is not None:
                (ons, ccs), self._swap = self._swap, None
                batch, k, i = [], 0, ons.first_after(done)
                j = next((n for n, (t, _) in enumerate(ccs) if t > done), len(ccs))
                # (the CC lines changed: each channel's values where it is now, heard at once; channels the lines
                # no longer use put back, so nothing is left on them for what plays there)
                now = cc_before(ccs, done + 1)
                kinds = {_cc_kind(m) for _, m in ccs}
                kinds |= {(ch, None) for ch, kind in kinds if kind == "bend"}
                for m in cc_reset({kind for kind in sent - kinds if kind[1] is not None}):
                    send(m)
                sent &= kinds
                for m in now:
                    send(m)
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
            t_now, tick_now, spt = self._clock
            t1 = self.stop_beat * self.ppq
            if tick is None or tick >= t1:  # nothing more to play: wait for the end
                wait = t_now + (t1 - tick_now) * spt - clock()
                if wait <= 0:
                    return
                sleep(min(wait, 0.01))
                continue
            wait = t_now + (tick - tick_now) * spt - clock()
            if wait > 0:
                sleep(min(wait, 0.01))
                continue  # (stopped, new notes or a new tempo meanwhile: looked at again)
            done = tick
            if other:
                send(c[1])
                sent.add(_cc_kind(c[1]))
                j += 1
                continue
            if off:
                key = heapq.heappop(offs)[1]
                n = held.get(key, 0) - 1
                # (31-EDO: every colour's notes on a row share its one channel, so the row sounds until its last
                # note ends, like the colours' own channels do in 12-tone; shapes keeping their colours the same)
                if n <= 0 or not shared:
                    send(0x80 | key)
                if n > 0:
                    held[key] = n
                else:
                    held.pop(key, None)
                continue
            k += 1
            if wait < -0.1:
                continue  # far behind (too many notes at once): skip late notes instead of smearing them
            s, e, key, vel = on
            send(0x90 | key | vel << 16)
            held[key] = held.get(key, 0) + 1
            heapq.heappush(offs, (e, key))

    def stop(self):
        if self.thread is not None:
            self._stop.set()
            self.thread.join()
            self.thread = None
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
