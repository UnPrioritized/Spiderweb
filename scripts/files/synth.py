"""The built-in synth (the Hz bass preview): BASS + BASSMIDI by Un4seen Developments through ctypes.

The DLLs sit in scripts/bass/x64 and x86 (the one matching this Python is used; the .exe carries the 64-bit ones
inside); Linux's .so files in scripts/bass/linux-x64 (64-bit PCs only). They are free for non-commercial use only (README). Nothing here needs a MIDI-out device.

Sound is made ahead of time, not live: render() turns a stretch of the notes into sound (48 kHz stereo floats) and
can run in several threads at once, each on its own core (the DLL lets go of Python while it works). Speed depends
on the voice limit, hardly on the note count (measured: 1000 voices = about 4x real time on one core). A stretch
starts PREROLL seconds early and that part is thrown away, so notes from before it still sound and the voice limit
is already full, as in one long render. Player plays sound from anywhere (it asks for it piece by piece) through
Limiter, which keeps the level in bounds (raw BASSMIDI output peaks 10-16 times too loud with many keys).
Live is the other way: notes sounded the moment they're sent, like a MIDI-out device ("Built-in BASSMIDI" under
MIDI out: playback, listening with the mouse, the Hz window's keys), played by BASS itself without Player."""

import atexit
import ctypes
import math
import os
import struct
import sys
import threading
from collections import deque

import numpy as np

from files.about import HERE
from files.system import WINDOWS

RATE = 48000  # frames a second
PREROLL = 1.0  # seconds rendered before a stretch and thrown away. Measured: joined stretches differ from one long
# render by about -25 dB all through (partly reverb), no clicks where they meet; with no preroll the first 0.25 s
# are 34 % off.
PIECE = RATE // 20  # frames asked of BASS at a time (progress is told this often)
FADE_MS = 60  # stopping fades out this long (a sudden stop clicks)

# bass.h / bassmidi.h
_DEVICE_NONE, _DEVICE_DEFAULT = 0, -1
_ERROR_ALREADY = 14
_ERROR_CODEC = 44
_NO_DEVICE = 0xFFFFFFFF  # (BASS_GetDevice's "none")
# The soundfont picker's file types (Linux's picker matches capitals exactly: SGM.SF2 was hidden)
FONT_TYPES = "*.sf2 *.sf3 *.sfz *.sf2pack" + ("" if WINDOWS else " *.SF2 *.SF3 *.SFZ *.SF2PACK")
_SAMPLE_FLOAT, _STREAM_DECODE, _UNICODE = 0x100, 0x200000, 0x80000000
_DATA_FLOAT, _POS_BYTE, _STREAMPROC_END = 0x40000000, 0, 0x80000000
_MIDI_DECAYEND, _MIDI_NOFX, _MIDI_ASYNC = 0x1000, 0x2000, 0x400000
_ATTRIB_MIDI_VOICES, _ATTRIB_MIDI_VOICES_ACTIVE = 0x12003, 0x12004
_ATTRIB_MIDI_CPU, _ATTRIB_MIDI_SRC, _ATTRIB_MIDI_KILL, _ATTRIB_MIDI_QUEUE_ASYNC = 0x12001, 0x12006, 0x12007, 0x1200d
_ATTRIB_VOL, _ATTRIB_BUFFER = 2, 13
_CONFIG_UPDATEPERIOD = 1
_EV_END, _EV_NOTE, _EV_PROGRAM, _EV_TEMPO = 0, 1, 2, 62
_EVENTS_RAW = 0x10000

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


# (WINFUNCTYPE is Windows-only: elsewhere BASS uses the plain C way of calling)
_STREAMPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                                               ctypes.c_uint, ctypes.c_void_p)
_DSPPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(None, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                                            ctypes.c_uint, ctypes.c_void_p)
_dlls = None


def _pin(*paths):
    """(Windows) The DLLs stay loaded until Spiderweb ends. A MIDI-out device running on BASS (one measured 2026-10-07)
    shares ours and unloads them once too often as it closes: loaded again at its next use, BASSMIDI then
    crashed at the built-in synth's next stream (access violation)."""
    k32 = ctypes.WinDLL("kernel32")
    k32.GetModuleHandleExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    for path in paths:
        k32.GetModuleHandleExW(1, path, ctypes.byref(ctypes.c_void_p()))  # (1 = GET_MODULE_HANDLE_EX_FLAG_PIN)


def _load():
    """(bass, bassmidi), loaded once. SynthError when they can't be."""
    global _dlls
    if _dlls:
        return _dlls
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    folder = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "bass",
                          arch if WINDOWS else "linux-" + arch)
    try:
        if WINDOWS:
            bass = ctypes.WinDLL(os.path.join(folder, "bass.dll"))
            midi = ctypes.WinDLL(os.path.join(folder, "bassmidi.dll"))
            _pin(os.path.join(folder, "bass.dll"), os.path.join(folder, "bassmidi.dll"))
        else:  # (Linux, untested: libbassmidi.so needs libbass.so's names, so that one is shared first)
            bass = ctypes.CDLL(os.path.join(folder, "libbass.so"), mode=ctypes.RTLD_GLOBAL)
            midi = ctypes.CDLL(os.path.join(folder, "libbassmidi.so"))
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
                            ("BASS_ChannelSlideAttribute", [u, u, f, u], i),
                            ("BASS_ChannelGetAttribute", [u, u, ctypes.POINTER(f)], i),
                            ("BASS_ChannelSetDSP", [u, _DSPPROC, p, i], u), ("BASS_ChannelRemoveDSP", [u, u], i),
                            ("BASS_SetConfig", [u, u], i), ("BASS_GetConfig", [u], u),
                            ("BASS_SetDevice", [u], i), ("BASS_GetDevice", [], u)):
        fn = getattr(bass, name)
        fn.argtypes, fn.restype = args, res
    for name, args, res in (("BASS_MIDI_FontInit", [ctypes.c_wchar_p if WINDOWS else ctypes.c_char_p, u], u), ("BASS_MIDI_FontFree", [u], i),
                            ("BASS_MIDI_StreamCreateEvents", [p, u, u, u], u),
                            ("BASS_MIDI_StreamCreate", [u, u, u], u), ("BASS_MIDI_StreamEvents", [u, u, p, u], u),
                            ("BASS_MIDI_StreamSetFonts", [u, p, u], i), ("BASS_MIDI_StreamLoadSamples", [u], i),
                            ("BASS_MIDI_FontLoad", [u, i, i], i)):
        fn = getattr(midi, name)
        fn.argtypes, fn.restype = args, res
    _dlls = bass, midi
    # BASS stopped while Python can still answer: its sound thread asking a player for sound while Python shuts
    # down crashed the program on Linux (measured in WSL, a song playing when the window closed)
    atexit.register(bass.BASS_Free)
    return _dlls


def events(notes, ppq, bpm, chans=None):
    """The notes (rows start, end, key, velocity, ... in ticks) as BASSMIDI's event list: one channel (chans: each
    note's channel, never 10's), program 0, one tempo. At the same tick a note's end comes before the next start.
    Keys above 127 are left out."""
    notes = np.asarray(notes)
    keep = notes[:, 2] <= 127 if len(notes) else np.zeros(0, bool)
    notes = notes[keep] if len(notes) else notes.reshape(0, 4)
    n = len(notes)
    ev = np.zeros(2 * n + 3, EVENT)
    ev[0] = (_EV_TEMPO, int(round(60e6 / bpm)), 0, 0, 0)
    ev[1] = (_EV_PROGRAM, 0, 0, 0, 0)
    body = np.zeros(2 * n, EVENT)
    body["event"] = _EV_NOTE
    body["tick"][:n], body["tick"][n:] = notes[:, 0], notes[:, 1]
    body["param"][:n] = notes[:, 2] | (np.clip(notes[:, 3], 1, 127) << 8)
    body["param"][n:] = notes[:, 2]  # (velocity 0 = the note ends)
    if chans is not None:
        c = np.minimum(np.asarray(chans, np.int64)[keep], 14)
        body["chan"][:n] = body["chan"][n:] = c + (c >= 9)
    ev[2:-1] = body[np.lexsort((np.r_[np.ones(n), np.zeros(n)], body["tick"]))]
    ev[-1] = (_EV_END, 0, 0, ev[-2]["tick"], 0)
    return ev


class Synth:
    """BASS started once, with a soundfont. can_play = False when there's no sound device (render still works)."""

    def __init__(self):
        self.bass, self.midi = _load()
        self.play_device = None  # (BASS's number for the sound device, once it opened)
        if not self._init(_DEVICE_DEFAULT):
            self._init(_DEVICE_NONE)  # (sound can still be made, not played: the device is asked for again)
        self.font, self.font_path = 0, None
        self._lock = threading.Lock()
        self._users = {}  # font -> renders using it now (an old font is only closed when none are left)
        self._old = set()  # fonts replaced, to close once unused

    def _init(self, device):
        ok = bool(self.bass.BASS_Init(device, RATE, 0, None, None)) or \
            (self.bass.BASS_ErrorGetCode() == _ERROR_ALREADY and device == _DEVICE_DEFAULT)
        if ok and device == _DEVICE_DEFAULT:
            self.play_device = self.bass.BASS_GetDevice()
        return ok

    @property
    def can_play(self):
        """True when there's a sound device to play through, made this thread's for the stream about to be made.
        Not there at the start (headphones off) = asked again each time (hunt 2026-10-09: it stayed silent, with no
        message, until a restart)."""
        if self.play_device is None:
            self._init(_DEVICE_DEFAULT)
            if self.play_device is None:
                return False
        if self.play_device != _NO_DEVICE:
            self.bass.BASS_SetDevice(self.play_device)
        return True

    def set_font(self, path):
        """Opens a soundfont (.sf2 / .sfz); the old one is closed. SynthError if it can't be read."""
        # (file names: UTF-16 on Windows, UTF-8 bytes elsewhere)
        font = (self.midi.BASS_MIDI_FontInit(path, _UNICODE) if WINDOWS else
                self.midi.BASS_MIDI_FontInit(os.fsencode(path), 0))
        if not font:
            raise SynthError("synth.bad_font", name=os.path.basename(path), err=self.bass.BASS_ErrorGetCode())
        # A packed soundfont opens even when its samples' format can't be read (FLAC / WavPack need BASS add-ons
        # we don't ship): its notes would be silent, so the piano is loaded now to find out
        if path.lower().endswith(".sf2pack") and not self.midi.BASS_MIDI_FontLoad(font, 0, 0) and \
                self.bass.BASS_ErrorGetCode() == _ERROR_CODEC:
            self.midi.BASS_MIDI_FontFree(font)
            raise SynthError("synth.pack_codec", name=os.path.basename(path))
        with self._lock:
            old, self.font, self.font_path = self.font, font, path
            if old:
                self._old.add(old)
        self._free_unused()

    def _take(self):
        """The font to use, counted as in use until _give."""
        with self._lock:
            font = self.font
            self._users[font] = self._users.get(font, 0) + 1
        return font

    def _give(self, font):
        with self._lock:
            self._users[font] -= 1
        self._free_unused()

    def _free_unused(self):
        with self._lock:
            gone = [f for f in self._old if not self._users.get(f)]
            self._old.difference_update(gone)
        for f in gone:
            self.midi.BASS_MIDI_FontFree(f)

    def warm_up(self):
        """Loads the soundfont's piano (program 0) now, so the first piece of sound doesn't wait for it."""
        ev = events(np.array([[0, 1, 60, 1]]), 960, 120)
        h = self.midi.BASS_MIDI_StreamCreateEvents(ev.ctypes.data, 960, _STREAM_DECODE | _SAMPLE_FLOAT, RATE)
        if h:
            used = self._take()
            font = _Font(used, -1, 0)
            self.midi.BASS_MIDI_StreamSetFonts(h, ctypes.byref(font), 1)
            self.midi.BASS_MIDI_StreamLoadSamples(h)
            self.bass.BASS_StreamFree(h)
            self._give(used)

    def render(self, ev, ppq, start, frames, voices, nofx=False, cancel=None, progress=None, stats=None):
        """`frames` frames of sound from `start` (frames from the song's start) as float32 rows (left, right).
        cancel = a threading.Event (set = stop: returns None); progress(frames done) after every piece;
        stats (a dict) gets "voices" = the most voices used at once, and "end" = the frame where all sound has died
        away after the last note, if that's in this stretch."""
        if not self.font:
            raise SynthError("synth.no_font")
        bass = self.bass
        flags = _STREAM_DECODE | _SAMPLE_FLOAT | _MIDI_DECAYEND | (_MIDI_NOFX if nofx else 0)
        h = self.midi.BASS_MIDI_StreamCreateEvents(ev.ctypes.data, ppq, flags, RATE)
        if not h:
            raise SynthError("synth.failed", err=bass.BASS_ErrorGetCode())
        sf = self._take()
        try:
            font = _Font(sf, -1, 0)
            self.midi.BASS_MIDI_StreamSetFonts(h, ctypes.byref(font), 1)
            bass.BASS_ChannelSetAttribute(h, _ATTRIB_MIDI_VOICES, float(voices))
            self.midi.BASS_MIDI_StreamLoadSamples(h)
            # past the last note (the stream can't jump there): from PREROLL before the last note ends, so the
            # ring after it is heard
            length = bass.BASS_ChannelGetLength(h, _POS_BYTE) // 8
            at = min(start - min(start, int(PREROLL * RATE)), max(0, length - int(PREROLL * RATE)))
            early = start - at
            out = np.zeros((early + frames, 2), np.float32)
            if at and not bass.BASS_ChannelSetPosition(h, at * 8, _POS_BYTE):
                return out[early:]  # (a failed jump would start from the song's start instead)
            done, most, used = 0, 0.0, ctypes.c_float()
            while done < len(out):
                if cancel is not None and cancel.is_set():
                    return None
                n = min(PIECE, len(out) - done)
                got = bass.BASS_ChannelGetData(h, out[done:].ctypes.data, (n * 8) | _DATA_FLOAT)
                if got <= 0:  # (the ring after the last note has died away: silence from here)
                    if stats is not None:
                        stats["end"] = at + done
                    break
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
            self._give(sf)

    def close(self):
        """(After every render has finished.)"""
        for f in self._old | ({self.font} if self.font else set()):
            self.midi.BASS_MIDI_FontFree(f)
        self._old.clear()
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
    array = the end. Limiter + volume (0..1) on the way out. position() = the frame of pull's sound heard now.
    buffer = seconds of sound BASS keeps ready (None: its own half second; the live keys want a short one)."""

    def __init__(self, synth, pull, volume=0.8, buffer=None):
        self.synth, self.pull, self.volume = synth, pull, volume
        self.limiter = Limiter()
        self.heard = deque()  # (stream frame, song frame or None = silence, count): what was handed to BASS
        self.written = 0
        self.fed = 0  # song frames handed over (the limiter holds the last Limiter.DELAY of them)
        self._proc = _STREAMPROC(self._fill)  # (kept: BASS calls it)
        self.handle = synth.bass.BASS_StreamCreate(RATE, 2, _SAMPLE_FLOAT, self._proc, None)
        if not self.handle:
            raise SynthError("synth.failed", err=synth.bass.BASS_ErrorGetCode())
        if buffer is not None:
            synth.bass.BASS_ChannelSetAttribute(self.handle, _ATTRIB_BUFFER, float(buffer))
            # BASS refills every "update period" (100 ms to start): one longer than the buffer plays it, then
            # silence till the next refill (measured: 0.06 s buffer = 60 % of the sound, a 10-a-second stutter)
            ms = max(5, int(buffer * 1000 / 4))
            if synth.bass.BASS_GetConfig(_CONFIG_UPDATEPERIOD) > ms:
                synth.bass.BASS_SetConfig(_CONFIG_UPDATEPERIOD, ms)

    def _fill(self, handle, buffer, length, user):
        n = length // 8
        out = np.frombuffer((ctypes.c_float * (n * 2)).from_address(buffer), np.float32).reshape(n, 2)
        if not self.handle:  # (fading out after stop: nothing more from pull)
            out[:] = 0
            return length
        parts, m, end = [], 0, False  # (pull may hand over less than asked, e.g. up to the end of a piece: ask again)
        while m < n:
            try:
                got = self.pull(n - m)
            except Exception:
                from files.errors import write_log
                write_log(*sys.exc_info(), "synth player")
                got = np.zeros((0, 2), np.float32)
            if got is None:
                break
            if not len(got):
                end = True
                break
            parts.append(np.asarray(got, np.float32))
            m += len(got)
        if m:
            out[:m] = self.limiter.process(np.concatenate(parts)) * self.volume
            self.heard.append((self.written, self.fed - Limiter.DELAY, m))
            self.written += m
            self.fed += m
        if end:
            return (m * 8) | _STREAMPROC_END
        if m < n:  # (not made yet: silence, and the song waits)
            out[m:] = 0
            self.heard.append((self.written, None, n - m))
            self.written += n - m
        return length

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

    def stop(self, fade=True):
        """Stops: a quick fade out (FADE_MS, so it doesn't click), then the stream is freed."""
        h, self.handle = self.handle, 0
        if not h:
            return
        bass = self.synth.bass
        if fade and bass.BASS_ChannelIsActive(h) and bass.BASS_ChannelSlideAttribute(h, _ATTRIB_VOL, -1.0, FADE_MS):
            # (-1 = stops when faded; the timer keeps this Player, so its _proc lives while BASS may still call it)
            timer = threading.Timer(FADE_MS / 1000 + 0.1, lambda: (bass.BASS_StreamFree(h), self))
            timer.daemon = True
            timer.start()
        else:
            bass.BASS_ChannelStop(h)
            bass.BASS_StreamFree(h)


# Built-in BASSMIDI's limiter (Live.set_limiter): Limiter's way (gain worked out per block, looking one block ahead,
# ceiling at full volume) but with all it keeps in one row of numbers, so BASS's sound thread can run it as compiled
# code that never waits for Python (fastloops.limit_dsp, when Numba is there) or else live_limit below (the same
# maths; dev/tests/live_limiter.py compares them). Switched on / off, it fades between the plain and the limited
# sound over LIM_FADE (a cut clicks: the limited sound is LIM_DELAY frames late); the first LIM_DELAY frames after
# it's put on the stream stay plain (nothing late to fade to yet).
LIM_BLOCK, LIM_DELAY, LIM_RING = 64, 128, 256  # frames: a gain per block, the limited sound this late, frames kept
LIM_N, LIM_ON, LIM_MIX, LIM_ENV, LIM_G0, LIM_G1, LIM_PEAK, LIM_PREV = range(8)  # (the row: then the kept frames)
LIM_HEAD = 8
LIM_SIZE = LIM_HEAD + 2 * LIM_RING
LIM_CEILING = 1.0  # full volume (the driver's limiter plugin at its defaults)
LIM_FALL = math.exp(-LIM_BLOCK / (Limiter.RELEASE * RATE))
LIM_FADE = 0.005  # seconds
LIM_STEP = 1 / (LIM_FADE * RATE)


def limit_state():
    st = np.zeros(LIM_SIZE)
    st[LIM_ENV], st[LIM_G0], st[LIM_G1] = LIM_CEILING, 1.0, 1.0
    return st


def live_limit(st, x):
    """The limiter without Numba: x = float32 rows (left, right), changed in place; st = limit_state()'s row."""
    ring = st[LIM_HEAD:].reshape(LIM_RING, 2)
    m, i = len(x), 0
    while i < m:  # (in pieces ending at block ends)
        n = int(st[LIM_N])
        k = min(m - i, LIM_BLOCK - n % LIM_BLOCK)
        a = x[i:i + k].astype(np.float64)
        r = n % LIM_RING
        ring[r:r + k] = a
        st[LIM_PEAK] = max(st[LIM_PEAK], np.abs(a).max())
        d = n - LIM_DELAY
        steps = LIM_STEP * np.arange(1, k + 1)
        if st[LIM_ON]:
            mix = np.minimum(1.0, st[LIM_MIX] + steps) if d >= 0 else np.full(k, st[LIM_MIX])
        else:
            mix = np.maximum(0.0, st[LIM_MIX] - steps)
        if d >= 0:
            t = (d % LIM_BLOCK + np.arange(1, k + 1)) / LIM_BLOCK
            gain = st[LIM_G0] * (1 - t) + st[LIM_G1] * t
            r = d % LIM_RING
            y = ring[r:r + k] * gain[:, None]
            x[i:i + k] = a * (1 - mix)[:, None] + y * mix[:, None]
        else:
            x[i:i + k] = a * (1 - mix)[:, None]
        st[LIM_MIX] = mix[-1]
        n += k
        st[LIM_N] = n
        if n % LIM_BLOCK == 0:  # a block made: the gain at the end of the one before it (the next to come out)
            if n >= 2 * LIM_BLOCK:
                st[LIM_ENV] = max(st[LIM_PREV], st[LIM_PEAK], st[LIM_ENV] * LIM_FALL)
                st[LIM_G0], st[LIM_G1] = st[LIM_G1], LIM_CEILING / max(st[LIM_ENV], LIM_CEILING)
            st[LIM_PREV], st[LIM_PEAK] = st[LIM_PEAK], 0.0
        i += k


class Live:
    """A BASSMIDI stream with 16 channels (10 = drums, as on any GM synth) that sounds each message the moment it's
    sent, set up like the common BASSMIDI MIDI-out driver (user, 2026-10-07: Player's Limiter + Python feeding the
    sound pumped and stuttered next to it): BASS plays the stream itself, straight into the sound device's mix (no
    Python in the sound thread, so a busy window can't starve it), full volume and no limiter (too loud = clipped,
    as there), linear sample interpolation, notes killed rather than faded at the voice limit, voices dropped
    rather than stuttering when it can't keep up. Holds the soundfont open until close(). set_limiter = the
    driver's optional limiter (a limiter plugin at its defaults: catches only what goes past full volume), handed
    each piece by BASS as it makes it (see LIM_BLOCK; BASS's own compressor measured useless here: at full volume it
    does nothing, past +6 dB it lets sound through). Without Numba that step is Python: it waits while the window is
    busy and the sound breaks up then (hunt 2026-10-08: editing while playing, 150 ms of 6 s missing)."""
    CPU = 95  # % of the time it may spend making sound before voices are dropped (the driver's default)
    QUEUE = 65536 * 4  # bytes of messages waiting for the sound thread (the driver's default)
    def __init__(self, synth, voices, volume=1.0, nofx=False, limiter=False):
        if not synth.font:
            raise SynthError("synth.no_font")
        if not synth.can_play:
            raise SynthError("synth.no_device")
        bass, midi = synth.bass, synth.midi
        self.synth, self.font_path = synth, synth.font_path
        self.handle = midi.BASS_MIDI_StreamCreate(16, _SAMPLE_FLOAT | _MIDI_ASYNC | (_MIDI_NOFX if nofx else 0),
                                                  RATE)
        if not self.handle:
            raise SynthError("synth.failed", err=bass.BASS_ErrorGetCode())
        self.font = synth._take()
        font = _Font(self.font, -1, 0)
        midi.BASS_MIDI_StreamSetFonts(self.handle, ctypes.byref(font), 1)
        h, at = self.handle, bass.BASS_ChannelSetAttribute
        for attr, value in ((_ATTRIB_BUFFER, 0), (_ATTRIB_MIDI_SRC, 0), (_ATTRIB_MIDI_KILL, 1),
                            (_ATTRIB_MIDI_CPU, self.CPU), (_ATTRIB_MIDI_QUEUE_ASYNC, self.QUEUE),
                            (_ATTRIB_VOL, volume)):
            at(h, attr, float(value))
        self.set_voices(voices)
        self.dsp, self.st, self._proc = 0, None, None  # (st + _proc kept: BASS holds on to them)
        self._lock = threading.Lock()
        if limiter:
            self.set_limiter(True)
        midi.BASS_MIDI_StreamLoadSamples(h)  # (program 0 + the drums: no wait at the first notes)
        if not bass.BASS_ChannelPlay(h, 0):
            err = bass.BASS_ErrorGetCode()
            self.close()
            raise SynthError("synth.failed", err=err)

    def set_voices(self, voices):
        self.synth.bass.BASS_ChannelSetAttribute(self.handle, _ATTRIB_MIDI_VOICES, float(voices))

    def set_limiter(self, on):
        """Heard at once (faded over LIM_FADE). Turned off, it leaves the stream once faded out."""
        with self._lock:
            if not self.handle:
                return
            if on and not self.dsp:
                from files import speed
                fast = speed.loops()
                if fast is not None and struct.calcsize("P") == 8:  # (compiled code calls the plain C way: 64-bit)
                    self._proc = _DSPPROC(fast.limit_dsp.address)
                else:
                    self._proc = _DSPPROC(self._limit)
                self.st = limit_state()
                self.dsp = self.synth.bass.BASS_ChannelSetDSP(self.handle, self._proc, self.st.ctypes.data, 0)
            if self.dsp:
                self.st[LIM_ON] = 1.0 if on else 0.0
                if not on:
                    self._let_go_later()

    def _let_go_later(self):
        timer = threading.Timer(LIM_FADE + 0.05, self._let_go)
        timer.daemon = True
        timer.start()

    def _let_go(self):
        with self._lock:
            if not self.dsp or not self.handle or self.st[LIM_ON]:
                return
            if self.st[LIM_MIX] > 0:  # (not faded out yet: the sound device stalled)
                self._let_go_later()
                return
            self.synth.bass.BASS_ChannelRemoveDSP(self.handle, self.dsp)
            self.dsp = 0

    def _limit(self, dsp, channel, buf, length, user):
        """(BASS's sound thread, without Numba) A piece just made, turned down in place where too loud."""
        try:
            live_limit(self.st, np.ctypeslib.as_array(ctypes.cast(buf, ctypes.POINTER(ctypes.c_float)),
                                                      (length // 8, 2)))
        except Exception:  # (never into BASS's thread: that piece just plays as made)
            pass

    def send(self, msg):
        """One short MIDI message (status | data1 << 8 | data2 << 16, as a MIDI-out device takes it)."""
        if self.handle:
            self.synth.midi.BASS_MIDI_StreamEvents(self.handle, _EVENTS_RAW, ctypes.byref(ctypes.c_uint32(msg)), 3)

    def position(self):
        """Bytes of sound made so far (8 a frame)."""
        return self.synth.bass.BASS_ChannelGetPosition(self.handle, _POS_BYTE) if self.handle else 0

    def voices_playing(self):
        used = ctypes.c_float()
        if not self.handle or not self.synth.bass.BASS_ChannelGetAttribute(self.handle, _ATTRIB_MIDI_VOICES_ACTIVE,
                                                                            ctypes.byref(used)):
            return 0
        return int(used.value)

    def close(self):
        with self._lock:
            h, self.handle = self.handle, 0
        if not h:
            return
        bass = self.synth.bass
        if bass.BASS_ChannelIsActive(h) and bass.BASS_ChannelSlideAttribute(h, _ATTRIB_VOL, -1.0, FADE_MS):
            # (a quick fade out like Player.stop: cut off mid-sound it clicked; the timer keeps this Live, so the
            # limiter's _proc lives while BASS may still call it)
            timer = threading.Timer(FADE_MS / 1000 + 0.1, lambda: (bass.BASS_StreamFree(h), self.synth._give(self.font),
                                                                  self))
            timer.daemon = True
            timer.start()
        else:
            bass.BASS_StreamFree(h)
            self.synth._give(self.font)
