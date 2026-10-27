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
import threading
import time

import numpy as np

from files.about import HERE
from files.synth import RATE, SynthError, _ATTRIB_BUFFER, _SAMPLE_FLOAT, _STREAMPROC
from files.system import WINDOWS

DLL_NAME = "kestrel_bridge.dll"
BLOCK = 512  # frames a block (~11 ms): the default delay
AHEAD = 6  # blocks kept made ahead of the speakers (3 = every swing of the note budget was a gap, 2026-10-27)
MAX_VOICES = 1 << 20  # Kestrel's own default
BLOCKS = (256, 512, 1024, 2048)  # (Settings → Sound's block sizes)
VOICES_RANGE = (1024, 1 << 26)  # (Settings → Sound's Max voices)
# Settings → Sound's rows (App.kestrel_opts = Kestrel's keywords): "lower" changes while open, the rest at the next open
KESTREL_DEFAULTS = {"voices": MAX_VOICES, "lower": True, "block": BLOCK, "limiter": True, "dc_blocker": True}
UNKNOWN_LEN = 0xFFFFFFFF  # (push lens: a note whose length isn't known)
# (one Kestrel starting at a time, whatever asks: two starting together in one program can hang on the graphics
# card, seen in the connector's tests 2026-10-27)
_OPENING = threading.Lock()


class _Options(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("sample_rate", ctypes.c_uint32), ("block_frames", ctypes.c_uint32),
                ("ahead_blocks", ctypes.c_uint32), ("max_voices", ctypes.c_uint32),
                ("lower_by_itself", ctypes.c_uint32), ("limiter", ctypes.c_uint32),
                ("limiter_ceiling_db", ctypes.c_float), ("dc_blocker", ctypes.c_uint32),
                ("dc_blocker_hz", ctypes.c_float)]


class _Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("rendered", "handed", "silence", "late", "voices",
                                                      "budget_dropped", "kestrel_dropped", "stolen")] + \
               [(name, ctypes.c_uint32) for name in ("last_block_us", "worst_block_us", "allowance", "failed",
                                                      "last_prep_us", "voice_cap", "cuts")] + \
               [(name, ctypes.c_uint64) for name in ("keys_wanted", "keys_kept", "fast_blocks", "merged")] + \
               [(name, ctypes.c_float) for name in ("merge_mean", "merge_max")]


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
                            ("kb_push", [p, p, p, p, u32], None), ("kb_push_len", [p, p, p, p, p, u32], None),
                            ("kb_push_copies", [p, p, p, p, p, p, u32], None),
                            ("kb_cancel", [p], None), ("kb_song", [p, ctypes.c_double, u32], None),
                            ("kb_cut", [p, ctypes.c_uint64], ctypes.c_uint64),
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

    def __init__(self, synth, fonts, block=BLOCK, ahead=AHEAD, voices=MAX_VOICES, lower=True, limiter=True,
                 ceiling_db=0.0, dc_blocker=True, dc_blocker_hz=0.0):
        d = self.dll = load()
        self.bass = synth.bass
        self.block = block
        opts = _Options(ctypes.sizeof(_Options), RATE, block, ahead, voices, int(lower), int(limiter), ceiling_db,
                        int(dc_blocker), dc_blocker_hz)
        names = (ctypes.c_char_p * max(1, len(fonts)))(*[os.fsdecode(f).encode("utf-8") for f in fonts])
        with _OPENING:
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

    def push_copies(self, frames, msgs, tracks, copies):
        """push, each note-on standing for copies[i] exact copies sounding together (their note-offs left out by
        the caller); arrays of the right types already (uint64, uint32, uint16, uint32), contiguous."""
        n = len(msgs)
        if n and self.handle:
            self.dll.kb_push_copies(self.handle, frames.ctypes.data, msgs.ctypes.data, tracks.ctypes.data, None,
                                    copies.ctypes.data, n)

    def push(self, frames, msgs, tracks, lens=None):
        """Queue messages: frames (output frame each), msgs (status | data1 << 8 | data2 << 16 | port << 24),
        tracks (the track each belongs to: the note budget shares by it), lens (each note-on's length in frames,
        UNKNOWN_LEN = not known; the note budget keeps the longest start of a key). Arrays or lists of the same
        length. Push whole blocks (frames up to a multiple of block), well ahead: busy blocks are put in order
        here, on the caller's thread."""
        n = len(msgs)
        if not n or not self.handle:
            return
        f = np.ascontiguousarray(frames, np.uint64)
        m = np.ascontiguousarray(msgs, np.uint32)
        t = np.ascontiguousarray(tracks, np.uint16)
        if lens is None:
            self.dll.kb_push(self.handle, f.ctypes.data, m.ctypes.data, t.ctypes.data, n)
        else:
            ln = np.ascontiguousarray(lens, np.uint32)
            self.dll.kb_push_len(self.handle, f.ctypes.data, m.ctypes.data, t.ctypes.data, ln.ctypes.data, n)

    def cancel(self):
        """Forget every message not made into sound yet (sounding notes go on: send their note-offs after)."""
        if self.handle:
            self.dll.kb_cancel(self.handle)

    def cut(self, frame):
        """What push_copies sent from frame on taken back (never sound already made): the frame it was taken back
        from, or None (the connector didn't answer: nothing taken back)."""
        if not self.handle:
            return None
        at = self.dll.kb_cut(self.handle, int(frame))
        return None if at == 2 ** 64 - 1 else int(at)

    def set_song(self, origin, song):
        """The song being pushed (a number, 0 = none) and the output frame its second 0 falls on: each place in the
        song keeps the note limits it had the first time it played (the same sound every play)."""
        if self.handle:
            self.dll.kb_song(self.handle, float(origin), int(song) & 0xFFFFFFFF)

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

    def anchor(self, ms=30):
        """Which frame the speakers get at which perf_counter time: (time, frame), from the counter of frames handed
        out read for `ms` (it jumps a device pull at a time: the reading right after a pull is the truest)."""
        best = None
        end = time.perf_counter() + ms / 1000
        while True:
            now = time.perf_counter()
            f = self.frame()
            if best is None or f - (now - best[0]) * RATE > best[1]:
                best = (now, f)
            if now >= end:
                return best
            time.sleep(0.001)

    def close(self):
        """Stream freed first (it calls into the connector), then the connector."""
        if self.stream:
            self.bass.BASS_StreamFree(self.stream)
            self.stream = 0
        if self.handle:
            self.dll.kb_close(self.handle)
            self.handle = None


LEAD = 0.2  # seconds the player sends ahead of the sound (more than the blocks made ahead: AHEAD x BLOCK = 64 ms)


class KestrelOut:
    """Kestrel as a MIDI out (playback.MidiOut's handle): send() = sounds now (Stop's note-offs); a player that knows
    what comes sends `lead` seconds early with send_at(msg, when) = the exact perf_counter time it should sound,
    stamped with that frame, so notes keep their exact time (not one block's). Messages are pushed in whole blocks
    (flush): a busy block's note-ons ranked together. track = the channel + port (the note budget shares by it)."""

    lead = LEAD

    def __init__(self, synth, fonts, **kw):
        self.k = Kestrel(synth, fonts, **kw)
        self.opts = dict(kw)  # (App.fresh_builtin: other settings = opened again)
        self.font_paths = list(fonts)  # (App.fresh_builtin: another list picked = opened again)
        self._lock = threading.Lock()
        self._frames, self._msgs, self._lens = [], [], []
        self._offs = []  # (frame, msg) note-offs pushed ahead: sent again by cancel()
        self.late = 0  # messages that came for a frame already made (send() = now: Stop's, mostly)
        self.resync()

    def resync(self):
        """Frames against the clock again (each play's start: the sound card's clock drifts from perf_counter)."""
        self._t0, self._f0 = self.k.anchor()

    def frame_at(self, when):
        return max(0, int(round(self._f0 + (when - self._t0) * RATE)))

    def send(self, msg):
        self.send_at(msg, time.perf_counter())
        self.flush(everything=True)

    def send_at(self, msg, when, length=None):
        """msg sounds at perf_counter time `when`; length = a note-on's length in seconds (None: not known)."""
        f = self.frame_at(when)
        with self._lock:
            self._frames.append(f)
            self._msgs.append(msg)
            self._lens.append(UNKNOWN_LEN if length is None else min(int(length * RATE), UNKNOWN_LEN - 1))
            if msg & 0xF0 == 0x80 or (msg & 0xF0 == 0x90 and not (msg >> 16) & 0x7F):
                self._offs.append((f, msg))

    def flush(self, everything=False):
        """Push what was sent, up to the last whole block (everything: all of it)."""
        with self._lock:
            if not self._msgs:
                return
            if everything:
                n = len(self._msgs)
            else:
                edge = self.frame_at(time.perf_counter() + self.lead) // BLOCK * BLOCK
                n = next((i for i, f in enumerate(self._frames) if f >= edge), len(self._frames))
                if not n:
                    return
            fr, ms, ln = self._frames[:n], self._msgs[:n], self._lens[:n]
            del self._frames[:n], self._msgs[:n], self._lens[:n]
            if len(self._offs) > 4096:  # (only the ones still ahead are ever needed)
                now = self.k.frame()
                self._offs = [o for o in self._offs if o[0] >= now]
        tracks = [(m & 15) | ((m >> 24) & 15) << 4 for m in ms]
        made = self.k.stats()["rendered"]
        if fr[0] < made:  # (for a frame already made: sounds at the next block's start, a little late)
            self.late += sum(f < made for f in fr)
        self.k.push(fr, ms, tracks, ln)

    def cancel(self):
        """Everything sent ahead forgotten (Stop): its note-offs sound now instead (the player counted those notes
        as ended), so nothing is left ringing."""
        with self._lock:
            offs = [m for f, m in self._offs if f >= self.k.frame()]
            offs += [m for m in self._msgs if m & 0xF0 == 0x80 or (m & 0xF0 == 0x90 and not (m >> 16) & 0x7F)]
            self._frames, self._msgs, self._lens, self._offs = [], [], [], []
        self.k.cancel()
        now = self.frame_at(time.perf_counter())
        if offs:
            self.k.push([now] * len(offs), offs, [(m & 15) | ((m >> 24) & 15) << 4 for m in offs])

    def close(self):
        self.k.close()
