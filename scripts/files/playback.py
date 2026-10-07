"""Playback through a MIDI-OUT device: Windows' own (winmm.dll via ctypes, nothing to install) or the built-in synth
(BUILTIN, files/synth.py's Live; the only one elsewhere)."""

import ctypes
import heapq
import sys
import threading
import time
from ctypes import wintypes

import numpy as np

from files.lang import tr
from files.system import WINDOWS
from notes.engine import CHANNELS

MAPPER_NAME = "Windows default (MIDI Mapper)"
BUILTIN = "Built-in BASSMIDI"  # (the user's name for it; like a device's name it's saved, so not translated)
DEFAULT_DEVICE = MAPPER_NAME if WINDOWS else BUILTIN
MAPPER = 0xFFFFFFFF

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


def devices():
    """Names of the MIDI-OUT devices: Windows' default first, then the built-in synth."""
    return [MAPPER_NAME, BUILTIN] + _winmm_devices() if _winmm else [BUILTIN]


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

    def open(self, name):
        """Open the device called name. Returns an error message, or None when it's ready."""
        if self.handle and self.name == name:
            return None
        self.close()
        if name == BUILTIN:
            from files.synth import SynthError
            try:
                self.handle = self.make_live()
            except SynthError as e:
                return str(e)
            self.name = name
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
        from files.synth import SynthError, _load
        try:
            _load()
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
            else:
                _winmm.midiOutShortMsg(h, msg)

    def note(self, ch, pitch, vel):
        """Note on (vel 0 = note off). Keys above 127 (256 keys) can't be sent: skipped."""
        if pitch <= 127:
            self.send(0x90 | ch | pitch << 8 | vel << 16)

    def close(self):
        if self.handle:
            if self.name == BUILTIN:
                self.handle.close()
            else:
                _winmm.midiOutReset(self.handle)
                _winmm.midiOutClose(self.handle)
        self.handle = self.name = None


class _Ons:
    """The notes to play, in the order they start: only the order is worked out (the notes stay where they are, so
    millions take little memory), and they're turned into Python numbers a batch at a time as they're played."""

    def __init__(self, notes, t0, t1, use10):
        s = notes[:, 0]
        keep = np.flatnonzero((s >= t0) & (s < t1) & (notes[:, 2] <= 127))  # (a synth has 128 keys)
        starts = s[keep]
        if len(starts) > 1 and not (starts[1:] >= starts[:-1]).all():
            order = np.argsort(starts, kind="stable")
            keep, starts = keep[order], starts[order]
            del order
        self.notes, self.keep, self.starts, self.use10 = notes, keep, starts, use10

    def __len__(self):
        return len(self.keep)

    def first_after(self, tick):
        """Index of the first note starting after tick."""
        return int(np.searchsorted(self.starts, tick, "right"))

    def batch(self, i, size=4096):
        """Notes i.. as (start, end, channel | key << 8, velocity) tuples."""
        part = self.notes[self.keep[i:i + size]]
        slot = part[:, 4]
        ch = slot % 16 if self.use10 else np.array(CHANNELS, np.int64)[slot % len(CHANNELS)]  # (slot_track_channel)
        return list(zip(part[:, 0].tolist(), part[:, 1].tolist(), (ch | part[:, 2] << 8).tolist(),
                        part[:, 3].tolist()))


class Player:
    """Plays notes on a background thread so the window stays responsive; position() says where it is. The notes
    and tempo can change while it plays (update): it plays on with the new ones from where it is."""

    def __init__(self, out):
        self.out = out
        self.thread = None
        self._stop = threading.Event()
        self._switch = sys.getswitchinterval()
        self.held = {}  # (channel | key << 8) -> how many notes on it are sounding right now
        self._clock = None
        self._swap = None  # new notes ready for the playing thread (_Ons)
        self._wanted = None  # new notes waiting to be put in order (notes, use10)
        self._sorting = None  # the thread putting them in order
        self._gen = 0  # one for each start (a sort left from an earlier play is thrown away)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, notes, ppq, bpm, start_beat, stop_beat, use10=False):
        """Play from start_beat until stop_beat (notes: rendered [start, end, pitch, vel, slot, ...] in ticks, never
        changed in place; use10: the slots use channel 10 too, engine.slot_track_channel)."""
        self.stop()
        self.ppq, self.start_beat, self.stop_beat = ppq, start_beat, stop_beat
        self.spt = 60 / bpm / ppq  # seconds a tick
        self._clock = None  # (the line waits at the start while the notes are put in order)
        self._gen += 1
        self._swap = self._wanted = None
        self._stop.clear()
        sys.setswitchinterval(0.001)  # hand the thread the processor quickly while the window redraws
        self.thread = threading.Thread(target=self._run, args=(notes, use10), daemon=True)
        self.thread.start()

    def update(self, notes, bpm, stop_beat, use10=False):
        """The notes / tempo changed while playing (same PPQ): play on with them. Notes already sounding end where
        they would have; new notes starting before the line aren't played (like a start from the line)."""
        if not self.running:
            return
        self.stop_beat = stop_beat
        spt = 60 / bpm / self.ppq
        clock = self._clock
        if spt != self.spt and clock is not None:  # (the same spot in the song now, at the new speed)
            now = time.perf_counter()
            self._clock = (now, clock[1] + (now - clock[0]) / clock[2], spt)
        self.spt = spt
        self._wanted = (self._gen, notes, use10)
        if self._sorting is None or not self._sorting.is_alive():
            self._sorting = threading.Thread(target=self._sort, daemon=True)
            self._sorting.start()

    def _sort(self):
        while self._wanted is not None:
            (gen, notes, use10), self._wanted = self._wanted, None
            ons = _Ons(notes, self.start_beat * self.ppq, self.stop_beat * self.ppq, use10)
            if self._wanted is None and gen == self._gen:  # (changed again meanwhile: those first)
                self._swap = ons

    def position(self):
        """Current beat."""
        clock = self._clock
        if clock is None:
            return self.start_beat
        t, tick, spt = clock
        return min(self.stop_beat, (tick + (time.perf_counter() - t) / spt) / self.ppq)

    def _run(self, notes, use10):
        clock, sleep, stop = time.perf_counter, time.sleep, self._stop
        send = self.out.send
        t0 = self.start_beat * self.ppq
        ons = _Ons(notes, t0, self.stop_beat * self.ppq, use10)
        del notes
        self._clock = (clock(), t0, self.spt)
        held = self.held = {}
        offs = []  # (end tick, channel | key << 8) of the notes sounding
        batch, k, i = [], 0, 0
        done = t0 - 1  # the last tick played
        while True:
            if stop.is_set():
                return
            if self._swap is not None:
                ons, self._swap = self._swap, None
                batch, k, i = [], 0, ons.first_after(done)
            if k == len(batch) and i < len(ons):
                batch, k = ons.batch(i), 0
                i += len(batch)
            on = batch[k] if k < len(batch) else None
            off = bool(offs) and (on is None or offs[0][0] <= on[0])  # (note-offs before note-ons on the same tick)
            tick = offs[0][0] if off else on[0] if on is not None else None
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
            if off:
                key = heapq.heappop(offs)[1]
                send(0x80 | key)
                n = held.get(key, 0) - 1
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
            # one for each note on a key (overlapping notes there each got a note-on)
            for key, n in self.held.items():
                for _ in range(n):
                    self.out.send(0x80 | key)
            self.held = {}
            self._swap = self._wanted = None
            sys.setswitchinterval(self._switch)
