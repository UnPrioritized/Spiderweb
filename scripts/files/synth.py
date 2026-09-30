"""The built-in synth (the Hz bass preview): BASS + BASSMIDI by Un4seen Developments through ctypes.

The DLLs sit in scripts/bass/x64 and x86 (the one matching this Python is used; the .exe carries the 64-bit ones
inside). They are free for non-commercial use only (README). Nothing here needs a MIDI-out device.

Sound is made ahead of time, not live: render() turns a stretch of the notes into sound (48 kHz stereo floats) and
can run in several threads at once, each on its own core (the DLL lets go of Python while it works). Speed depends
on the voice limit, hardly on the note count (measured: 1000 voices = about 4x real time on one core). A stretch
starts PREROLL seconds early and that part is thrown away, so notes from before it still sound and the voice limit
is already full, as in one long render. Player plays sound from anywhere (it asks for it piece by piece) through
Limiter, which keeps the level in bounds (raw BASSMIDI output peaks 10-16 times too loud with many keys)."""

import ctypes
import math
import os
import struct
import sys
import threading
from collections import deque

import numpy as np

from files.about import HERE

RATE = 48000  # frames a second
PREROLL = 1.0  # seconds rendered before a stretch and thrown away. Measured: joined stretches differ from one long
# render by about -25 dB all through (partly reverb), no clicks where they meet; with no preroll the first 0.25 s
# are 34 % off.
PIECE = RATE // 20  # frames asked of BASS at a time (progress is told this often)

# bass.h / bassmidi.h
_DEVICE_NONE, _DEVICE_DEFAULT = 0, -1
_ERROR_ALREADY = 14
_SAMPLE_FLOAT, _STREAM_DECODE, _UNICODE = 0x100, 0x200000, 0x80000000
_DATA_FLOAT, _POS_BYTE, _STREAMPROC_END = 0x40000000, 0, 0x80000000
_MIDI_DECAYEND, _MIDI_NOFX = 0x1000, 0x2000
_ATTRIB_MIDI_VOICES, _ATTRIB_MIDI_VOICES_ACTIVE = 0x12003, 0x12004
_EV_END, _EV_NOTE, _EV_PROGRAM, _EV_TEMPO = 0, 1, 2, 62

EVENT = np.dtype([("event", "<u4"), ("param", "<u4"), ("chan", "<u4"), ("tick", "<u4"), ("pos", "<u4")])


class SynthError(Exception):
    """Something the user should be told: str() = the text (key = its en.json key)."""

    def __init__(self, key, **values):
        super().__init__(key)
        self.key, self.values = key, values

    def __str__(self):
        from files.lang import tr
        return tr(self.key, **self.values)


class _Font(ctypes.Structure):
    _fields_ = [("font", ctypes.c_uint), ("preset", ctypes.c_int), ("bank", ctypes.c_int)]


_STREAMPROC = ctypes.WINFUNCTYPE(ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p)
_dlls = None


def _load():
    """(bass, bassmidi), loaded once. SynthError when they can't be."""
    global _dlls
    if _dlls:
        return _dlls
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    folder = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "bass", arch)
    try:
        bass = ctypes.WinDLL(os.path.join(folder, "bass.dll"))
        midi = ctypes.WinDLL(os.path.join(folder, "bassmidi.dll"))
    except (AttributeError, OSError) as e:
        raise SynthError("synth.no_dll", err=e) from None
    u, i, p, f = ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.c_float
    for name, args, res in (("BASS_Init", [i, u, u, p, p], i), ("BASS_Free", [], i), ("BASS_ErrorGetCode", [], i),
                            ("BASS_StreamCreate", [u, u, u, _STREAMPROC, p], u), ("BASS_StreamFree", [u], i),
                            ("BASS_ChannelPlay", [u, i], i), ("BASS_ChannelStop", [u], i),
                            ("BASS_ChannelIsActive", [u], u),
                            ("BASS_ChannelGetData", [u, p, u], i),
                            ("BASS_ChannelSetPosition", [u, ctypes.c_uint64, u], i),
                            ("BASS_ChannelGetPosition", [u, u], ctypes.c_uint64),
                            ("BASS_ChannelGetLength", [u, u], ctypes.c_uint64),
                            ("BASS_ChannelSetAttribute", [u, u, f], i),
                            ("BASS_ChannelGetAttribute", [u, u, ctypes.POINTER(f)], i)):
        fn = getattr(bass, name)
        fn.argtypes, fn.restype = args, res
    for name, args, res in (("BASS_MIDI_FontInit", [ctypes.c_wchar_p, u], u), ("BASS_MIDI_FontFree", [u], i),
                            ("BASS_MIDI_StreamCreateEvents", [p, u, u, u], u),
                            ("BASS_MIDI_StreamSetFonts", [u, p, u], i), ("BASS_MIDI_StreamLoadSamples", [u], i)):
        fn = getattr(midi, name)
        fn.argtypes, fn.restype = args, res
    _dlls = bass, midi
    return _dlls


def events(notes, ppq, bpm):
    """The notes (rows start, end, key, velocity, ... in ticks) as BASSMIDI's event list: one channel, program 0,
    one tempo. At the same tick a note's end comes before the next start. Keys above 127 are left out."""
    notes = np.asarray(notes)
    notes = notes[notes[:, 2] <= 127] if len(notes) else notes.reshape(0, 4)
    n = len(notes)
    ev = np.zeros(2 * n + 3, EVENT)
    ev[0] = (_EV_TEMPO, int(round(60e6 / bpm)), 0, 0, 0)
    ev[1] = (_EV_PROGRAM, 0, 0, 0, 0)
    body = np.zeros(2 * n, EVENT)
    body["event"] = _EV_NOTE
    body["tick"][:n], body["tick"][n:] = notes[:, 0], notes[:, 1]
    body["param"][:n] = notes[:, 2] | (np.clip(notes[:, 3], 1, 127) << 8)
    body["param"][n:] = notes[:, 2]  # (velocity 0 = the note ends)
    ev[2:-1] = body[np.lexsort((np.r_[np.ones(n), np.zeros(n)], body["tick"]))]
    ev[-1] = (_EV_END, 0, 0, ev[-2]["tick"], 0)
    return ev


class Synth:
    """BASS started once, with a soundfont. can_play = False when there's no sound device (render still works)."""

    def __init__(self):
        self.bass, self.midi = _load()
        self.can_play = self._init(_DEVICE_DEFAULT) or self._init(_DEVICE_NONE)
        self.font, self.font_path = 0, None
        self._lock = threading.Lock()

    def _init(self, device):
        return bool(self.bass.BASS_Init(device, RATE, 0, None, None)) or \
            (self.bass.BASS_ErrorGetCode() == _ERROR_ALREADY and device == _DEVICE_DEFAULT)

    def set_font(self, path):
        """Opens a soundfont (.sf2 / .sfz); the old one is closed. SynthError if it can't be read."""
        font = self.midi.BASS_MIDI_FontInit(path, _UNICODE)
        if not font:
            raise SynthError("synth.bad_font", name=os.path.basename(path), err=self.bass.BASS_ErrorGetCode())
        with self._lock:
            old, self.font, self.font_path = self.font, font, path
        if old:
            self.midi.BASS_MIDI_FontFree(old)

    def warm_up(self):
        """Loads the soundfont's piano (program 0) now, so the first piece of sound doesn't wait for it."""
        ev = events(np.array([[0, 1, 60, 1]]), 960, 120)
        h = self.midi.BASS_MIDI_StreamCreateEvents(ev.ctypes.data, 960, _STREAM_DECODE | _SAMPLE_FLOAT, RATE)
        if h:
            font = _Font(self.font, -1, 0)
            self.midi.BASS_MIDI_StreamSetFonts(h, ctypes.byref(font), 1)
            self.midi.BASS_MIDI_StreamLoadSamples(h)
            self.bass.BASS_StreamFree(h)

    def render(self, ev, ppq, start, frames, voices, nofx=False, cancel=None, progress=None, stats=None):
        """`frames` frames of sound from `start` (frames from the song's start) as float32 rows (left, right).
        cancel = a threading.Event (set = stop: returns None); progress(frames done) after every piece;
        stats (a dict) gets "voices" = the most voices used at once."""
        if not self.font:
            raise SynthError("synth.no_font")
        bass = self.bass
        flags = _STREAM_DECODE | _SAMPLE_FLOAT | _MIDI_DECAYEND | (_MIDI_NOFX if nofx else 0)
        h = self.midi.BASS_MIDI_StreamCreateEvents(ev.ctypes.data, ppq, flags, RATE)
        if not h:
            raise SynthError("synth.failed", err=bass.BASS_ErrorGetCode())
        try:
            font = _Font(self.font, -1, 0)
            self.midi.BASS_MIDI_StreamSetFonts(h, ctypes.byref(font), 1)
            bass.BASS_ChannelSetAttribute(h, _ATTRIB_MIDI_VOICES, float(voices))
            self.midi.BASS_MIDI_StreamLoadSamples(h)
            early = min(start, int(PREROLL * RATE))
            out = np.zeros((early + frames, 2), np.float32)
            # past the last note: silence (the ring after it only comes in a stretch that starts before; a failed
            # jump would start from the song's start instead)
            if start >= bass.BASS_ChannelGetLength(h, _POS_BYTE) // 8:
                return out[early:]
            if start - early and not bass.BASS_ChannelSetPosition(h, (start - early) * 8, _POS_BYTE):
                return out[early:]
            done, most, used = 0, 0.0, ctypes.c_float()
            while done < len(out):
                if cancel is not None and cancel.is_set():
                    return None
                n = min(PIECE, len(out) - done)
                got = bass.BASS_ChannelGetData(h, out[done:].ctypes.data, (n * 8) | _DATA_FLOAT)
                if got <= 0:
                    break  # (past the end: silence)
                done += got // 8
                if stats is not None and bass.BASS_ChannelGetAttribute(h, _ATTRIB_MIDI_VOICES_ACTIVE,
                                                                       ctypes.byref(used)):
                    most = max(most, used.value)
                if progress and done > early:
                    progress(done - early)
            if stats is not None:
                stats["voices"] = int(most)
            return out[early:]
        finally:
            bass.BASS_StreamFree(h)

    def close(self):
        if self.font:
            self.midi.BASS_MIDI_FontFree(self.font)
            self.font = 0
        self.bass.BASS_Free()


class Limiter:
    """Keeps the sound under CEILING, like a mastering limiter: turns down at once where it's too loud (looking one
    block ahead, so nothing gets through) and back up slowly (RELEASE). Output is DELAY frames late."""
    BLOCK = 64
    DELAY = 2 * BLOCK
    CEILING = 0.5
    RELEASE = 0.3  # seconds to come back up by about two thirds

    def __init__(self):
        self.inp = np.zeros((self.BLOCK, 2), np.float32)  # not turned yet (the last block waits for the next one)
        self.out = [np.zeros((self.BLOCK, 2), np.float32)]  # turned, not handed out yet
        self.env, self.gain = self.CEILING, 1.0
        self.fall = math.exp(-self.BLOCK / (self.RELEASE * RATE))

    def process(self, x):
        """x = float32 rows (left, right); returns as many rows, DELAY frames late."""
        B = self.BLOCK
        inp = self.inp = np.concatenate([self.inp, x])
        k = len(inp) // B - 1  # blocks that have the next block to look at
        if k > 0:
            peaks = np.abs(inp[:(k + 1) * B]).reshape(k + 1, -1).max(axis=1)
            ahead = np.maximum(peaks[:-1], peaks[1:])
            env, fall, top = self.env, self.fall, self.CEILING
            gains = np.empty(k + 1)
            gains[0] = self.gain
            for i in range(k):
                env = max(float(ahead[i]), env * fall)
                gains[i + 1] = top / max(env, top)
            self.env, self.gain = env, gains[-1]
            t = (np.arange(1, B + 1) / B)[None, :]
            ramp = (gains[:-1, None] * (1 - t) + gains[1:, None] * t).reshape(-1, 1)
            self.out.append(inp[:k * B] * ramp.astype(np.float32))
            self.inp = inp[k * B:]
        out = np.concatenate(self.out)
        self.out = [out[len(x):]]
        return out[:len(x)]


class Player:
    """Plays sound through the default sound device. pull(n) is asked for up to n frames (float32 rows) at a
    time, from BASS's own thread: rows = play them, None = not ready yet (silence; the song waits there), an empty
    array = the end. Limiter + volume (0..1) on the way out. position() = the frame of pull's sound heard now."""

    def __init__(self, synth, pull, volume=0.8):
        self.synth, self.pull, self.volume = synth, pull, volume
        self.limiter = Limiter()
        self.heard = deque()  # (stream frame, song frame or None = silence, count): what was handed to BASS
        self.written = 0
        self.fed = 0  # song frames handed over (the limiter holds the last Limiter.DELAY of them)
        self._proc = _STREAMPROC(self._fill)  # (kept: BASS calls it)
        self.handle = synth.bass.BASS_StreamCreate(RATE, 2, _SAMPLE_FLOAT, self._proc, None)
        if not self.handle:
            raise SynthError("synth.failed", err=synth.bass.BASS_ErrorGetCode())

    def _fill(self, handle, buffer, length, user):
        n = length // 8
        out = np.frombuffer((ctypes.c_float * (n * 2)).from_address(buffer), np.float32).reshape(n, 2)
        try:
            got = self.pull(n)
        except Exception:
            from files.errors import write_log
            write_log(*sys.exc_info(), "synth player")
            got = np.zeros((0, 2), np.float32)
        if got is None:
            out[:] = 0
            self.heard.append((self.written, None, n))
            self.written += n
            return length
        m = len(got)
        if m:
            out[:m] = self.limiter.process(np.asarray(got, np.float32)) * self.volume
            self.heard.append((self.written, self.fed - Limiter.DELAY, m))
            self.written += m
            self.fed += m
        return m * 8 if m == n else (m * 8) | _STREAMPROC_END

    def play(self, start=0):
        """Starts playing; start = the song frame pull() will be asked for first (only used by position)."""
        self.fed = start
        self.synth.bass.BASS_ChannelPlay(self.handle, 0)

    def position(self):
        """The song frame heard right now (None = silence is playing, e.g. waiting for the render)."""
        now = self.synth.bass.BASS_ChannelGetPosition(self.handle, _POS_BYTE) // 8
        while len(self.heard) > 1 and self.heard[1][0] <= now:
            self.heard.popleft()
        if not self.heard:
            return None
        at, song, n = self.heard[0]
        return None if song is None else song + min(now - at, n)

    def active(self):
        """False once the end was played (or it was stopped)."""
        return bool(self.handle) and self.synth.bass.BASS_ChannelIsActive(self.handle) != 0

    def stop(self):
        if self.handle:
            self.synth.bass.BASS_ChannelStop(self.handle)
            self.synth.bass.BASS_StreamFree(self.handle)
            self.handle = 0
