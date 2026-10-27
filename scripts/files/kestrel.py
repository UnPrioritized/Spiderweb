"""Kestrel live: the GPU SoundFont synth Kestrel (by thtstickyboi; live interface by mbms) through Spiderweb's own
connector DLL (native/kestrel_bridge, scripts/kestrel/x64/kestrel_bridge.dll), by ctypes like BASS.

Messages go in with the output FRAME each should sound at (frames counted from 0 at open, 48 kHz): tempo is
Spiderweb's to work out, Kestrel never sees ticks. Sound comes out through one BASS stream whose STREAMPROC is the
connector's own kb_streamproc, so no Python runs in the audio path. The connector keeps a few blocks made ahead;
a message for a frame already made sounds at the start of the next block.

64-bit Windows only for now (Linux = a quiet fix later; no Mac, no 32-bit): available() says why not."""

import ctypes
import os
import struct
import sys

import numpy as np

from files.about import HERE
from files.synth import RATE, SynthError, _ATTRIB_BUFFER, _SAMPLE_FLOAT, _STREAMPROC
from files.system import WINDOWS

DLL_NAME = "kestrel_bridge.dll"
BLOCK = 512  # frames a block (~11 ms): the default delay
AHEAD = 6  # blocks kept made ahead of the speakers (3 = every swing of the note budget was a gap, 2026-10-27)
MAX_VOICES = 1 << 20  # Kestrel's own default


class _Options(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("sample_rate", ctypes.c_uint32), ("block_frames", ctypes.c_uint32),
                ("ahead_blocks", ctypes.c_uint32), ("max_voices", ctypes.c_uint32),
                ("lower_by_itself", ctypes.c_uint32)]


class _Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("rendered", "handed", "silence", "late", "voices",
                                                      "budget_dropped", "kestrel_dropped", "stolen")] + \
               [(name, ctypes.c_uint32) for name in ("last_block_us", "worst_block_us", "allowance", "failed",
                                                      "last_prep_us", "voice_cap", "cuts")]


_dll = None


def dll_path():
    return os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "kestrel", "x64", DLL_NAME)


def available():
    """None when Kestrel can be used here, else the text key saying why not."""
    if not WINDOWS or struct.calcsize("P") != 8:
        return "playback.kestrel_not_here"
    if not os.path.isfile(dll_path()):
        return "playback.kestrel_missing"
    return None


def load():
    """The connector DLL, loaded once. SynthError when it can't be."""
    global _dll
    if _dll:
        return _dll
    why = available()
    if why:
        raise SynthError(why)
    try:
        d = ctypes.CDLL(dll_path())
    except OSError as e:
        raise SynthError("playback.kestrel_failed", err=e) from None
    p, u32 = ctypes.c_void_p, ctypes.c_uint32
    for name, args, res in (("kb_version", [], ctypes.c_char_p), ("kb_error", [p], ctypes.c_char_p),
                            ("kb_open", [ctypes.POINTER(ctypes.c_char_p), u32, ctypes.POINTER(_Options)], p),
                            ("kb_push", [p, p, p, p, u32], None), ("kb_cancel", [p], None),
                            ("kb_set_lower", [p, u32], None), ("kb_stats", [p, ctypes.POINTER(_Stats)], None),
                            ("kb_fill", [p, p, u32], None), ("kb_close", [p], None)):
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    _dll = d
    return d


def version():
    """The connector's version text ("0.1.0; Kestrel 1.3.0 (...)"), or None when it can't be loaded."""
    try:
        return load().kb_version().decode("utf-8", "replace")
    except SynthError:
        return None


def _error(d, h=None):
    text = d.kb_error(h)
    return text.decode("utf-8", "replace") if text else "?"


class Kestrel:
    """One Kestrel synth, playing (silence while nothing is sent) from open until close(). fonts = Spiderweb's
    soundfont list, top first (each overrides the ones above it). synth = files.synth.Synth (BASS + the sound
    device). Opening takes seconds (the soundfonts load): not on the window's thread. SynthError when it can't."""

    def __init__(self, synth, fonts, block=BLOCK, ahead=AHEAD, voices=MAX_VOICES, lower=True):
        d = self.dll = load()
        self.bass = synth.bass
        self.block = block
        opts = _Options(ctypes.sizeof(_Options), RATE, block, ahead, voices, int(lower))
        names = (ctypes.c_char_p * max(1, len(fonts)))(*[os.fsdecode(f).encode("utf-8") for f in fonts])
        h = d.kb_open(names, len(fonts), ctypes.byref(opts))
        if not h:
            raise SynthError("playback.kestrel_failed", err=_error(d))
        self.handle = h
        if not synth.can_play:
            d.kb_close(h)
            raise SynthError("playback.kestrel_no_sound_device")
        # (the connector's own function as the stream's STREAMPROC; BUFFER 0 = BASS asks it straight from the
        # device's mixing, the connector already keeps blocks ready)
        self._proc = _STREAMPROC(ctypes.cast(d.kb_streamproc, ctypes.c_void_p).value)
        self.stream = self.bass.BASS_StreamCreate(RATE, 2, _SAMPLE_FLOAT, self._proc, h)
        if not self.stream:
            d.kb_close(h)
            raise SynthError("synth.failed", err=self.bass.BASS_ErrorGetCode())
        self.bass.BASS_ChannelSetAttribute(self.stream, _ATTRIB_BUFFER, 0.0)
        self.bass.BASS_ChannelPlay(self.stream, 0)

    def push(self, frames, msgs, tracks):
        """Queue messages: frames (output frame each), msgs (status | data1 << 8 | data2 << 16 | port << 24),
        tracks (the track each belongs to: the note budget shares by it). Arrays or lists of the same length."""
        n = len(msgs)
        if not n or not self.handle:
            return
        f = np.ascontiguousarray(frames, np.uint64)
        m = np.ascontiguousarray(msgs, np.uint32)
        t = np.ascontiguousarray(tracks, np.uint16)
        self.dll.kb_push(self.handle, f.ctypes.data, m.ctypes.data, t.ctypes.data, n)

    def cancel(self):
        """Forget every message not made into sound yet (sounding notes go on: send their note-offs after)."""
        if self.handle:
            self.dll.kb_cancel(self.handle)

    def set_lower(self, on):
        if self.handle:
            self.dll.kb_set_lower(self.handle, int(on))

    def stats(self):
        """The connector's counters as a dict (see native/kestrel_bridge KbStats)."""
        s = _Stats()
        if self.handle:
            self.dll.kb_stats(self.handle, ctypes.byref(s))
        return {name: getattr(s, name) for name, _ in _Stats._fields_}

    def frame(self):
        """The frame being handed to the speakers now (+ the device's own small delay)."""
        return self.stats()["handed"]

    def error(self):
        """The render's error text once it failed, else None."""
        return _error(self.dll, self.handle) if self.handle and self.stats()["failed"] else None

    def close(self):
        """Stream freed first (it calls into the connector), then the connector."""
        if self.stream:
            self.bass.BASS_StreamFree(self.stream)
            self.stream = 0
        if self.handle:
            self.dll.kb_close(self.handle)
            self.handle = None
