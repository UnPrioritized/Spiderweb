"""Playback through a Windows MIDI-OUT device (winmm.dll via ctypes, nothing to install)."""

import ctypes
import sys
import threading
import time
from ctypes import wintypes

import numpy as np

from files.lang import tr
from notes.engine import CHANNELS

DEFAULT_DEVICE = "Windows default (MIDI Mapper)"
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


def devices():
    """Names of the MIDI-OUT devices, default first."""
    out = [DEFAULT_DEVICE]
    if not _winmm:
        return out
    for i in range(_winmm.midiOutGetNumDevs()):
        caps = _Caps()
        ok = _winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0
        out.append(caps.szPname if ok else tr("playback.device", i=i + 1))
    return out


class MidiOut:
    """One open MIDI-OUT device, kept open until another is picked."""

    def __init__(self):
        self.handle = None
        self.name = None

    def open(self, name):
        """Open the device called name. Returns an error message, or None when it's ready."""
        if self.handle and self.name == name:
            return None
        self.close()
        if not _winmm:
            return tr("playback.midi_playback_only_works_on_windows")
        names = devices()
        if name not in names:
            return tr("playback.the_midi_device_isn_t_there", name=name)
        dev = MAPPER if name == DEFAULT_DEVICE else names.index(name) - 1
        h = wintypes.HANDLE()
        err = _winmm.midiOutOpen(ctypes.byref(h), dev, 0, 0, 0)
        if err:
            return tr("playback.couldn_t_open_windows_error_another", name=name, err=err)
        self.handle, self.name = h, name
        return None

    def send(self, msg):
        if self.handle:
            _winmm.midiOutShortMsg(self.handle, msg)

    def note(self, ch, pitch, vel):
        """Note on (vel 0 = note off). Keys above 127 (256 keys) can't be sent: skipped."""
        if pitch <= 127:
            self.send(0x90 | ch | pitch << 8 | vel << 16)

    def close(self):
        if self.handle:
            _winmm.midiOutReset(self.handle)
            _winmm.midiOutClose(self.handle)
        self.handle = self.name = None


def batches(events, size=4096):
    """The event rows as Python tuples, a batch at a time (turning millions into Python numbers at once takes long)."""
    for i in range(0, len(events), size):
        yield from map(tuple, events[i:i + size].tolist())


class Player:
    """Plays notes on a background thread so the window stays responsive; position() says where it is."""

    def __init__(self, out):
        self.out = out
        self.thread = None
        self._stop = threading.Event()
        self._switch = sys.getswitchinterval()
        self.held = set()  # (channel, key) sounding right now

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, notes, ppq, bpm, start_beat, stop_beat):
        """Play from start_beat until stop_beat (notes: rendered [start, end, pitch, vel, slot, ...] in ticks)."""
        self.stop()
        t0, t1 = start_beat * ppq, stop_beat * ppq
        notes = notes[(notes[:, 0] >= t0) & (notes[:, 0] < t1) & (notes[:, 2] <= 127)]  # (a synth has 128 keys)
        s, e, p, v = notes[:, 0], notes[:, 1], notes[:, 2], notes[:, 3]
        ch = np.array(CHANNELS, np.int64)[notes[:, 4] % len(CHANNELS)]
        off = e < t1
        tick = np.concatenate([s, e[off]])
        on = np.concatenate([np.ones(len(s), np.int64), np.zeros(int(off.sum()), np.int64)])
        msg = np.concatenate([0x90 | ch | p << 8 | v << 16, (0x80 | ch | p << 8)[off]])
        order = np.lexsort((msg, on, tick))  # note-offs before note-ons on the same tick
        events = np.column_stack([tick[order], on[order], msg[order]])  # (tick, 1 = on, message) rows
        self.start_beat, self.stop_beat = start_beat, stop_beat
        self.beats_per_sec = bpm / 60
        self.sec_per_tick = 60 / bpm / ppq
        self._stop.clear()
        sys.setswitchinterval(0.001)  # hand the thread the processor quickly while the window redraws
        self.t_start = time.perf_counter()
        self.thread = threading.Thread(target=self._run, args=(events, t0), daemon=True)
        self.thread.start()

    def position(self):
        """Current beat."""
        return min(self.stop_beat, self.start_beat + (time.perf_counter() - self.t_start) * self.beats_per_sec)

    def _run(self, events, t0):
        clock, sleep, stop = time.perf_counter, time.sleep, self._stop
        send = self.out.send
        held = self.held = set()
        t_start, spt = self.t_start, self.sec_per_tick
        for tick, on, msg in batches(events):
            due = t_start + (tick - t0) * spt
            while True:
                if stop.is_set():
                    return
                wait = due - clock()
                if wait <= 0:
                    break
                sleep(min(wait, 0.01))
            if on and clock() - due > 0.1:
                continue  # far behind (too many notes at once): skip late notes instead of smearing them
            send(msg)
            key = (msg & 0x0F, msg >> 8 & 0x7F)
            if on:
                held.add(key)
            else:
                held.discard(key)
        end = t_start + (self.stop_beat - self.start_beat) / self.beats_per_sec
        while not stop.is_set() and clock() < end:
            sleep(min(end - clock(), 0.01))

    def stop(self):
        if self.thread is not None:
            self._stop.set()
            self.thread.join()
            self.thread = None
            # ordinary note-offs for what's still sounding, so the synth plays its release (a reset cuts it off)
            for ch, p in self.held:
                self.out.send(0x80 | ch | p << 8)
            self.held = set()
            sys.setswitchinterval(self._switch)
