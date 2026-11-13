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
import tempfile
import threading
import time

import numpy as np

from files.about import HERE
from files.synth import RATE, SynthError, _ATTRIB_BUFFER, _SAMPLE_FLOAT, _STREAMPROC
from files.system import WINDOWS

DLL_NAME = "kestrel_bridge.dll"
# (Linux: the same connector built there, Vulkan; "untested" on a real Linux PC: built + loaded in WSL 2026-10-28)
SO_NAME = "libkestrel_bridge.so"
BLOCK = 512  # frames a block (~11 ms): the default delay
AHEAD = 6  # blocks kept made ahead of the speakers (3 = every swing of the note budget was a gap, 2026-10-27)
MAX_VOICES = 1 << 20  # Kestrel's own default
BLOCKS = (256, 512, 1024, 2048)  # (Settings → Sound's block sizes)
VOICES_RANGE = (1024, 1 << 26)  # (Settings → Sound's Max voices)
# Settings → Sound's rows (App.kestrel_opts = Kestrel's keywords): "lower" changes while open, the rest at the next open
LIVE_VOICES = 300_000  # (Max voices to start with: user 2026-10-28, Kestrel's own 1 M fell behind on heavy MIDIs)
KESTREL_DEFAULTS = {"voices": LIVE_VOICES, "lower": True, "block": BLOCK,
                    # (user 2026-11-02, Kestrel 1.4.0: the limiter a list, Balanced first + the default; the
                    # connector's numbers 0 Off / 1 Brickwall (the old tick's "on") / 2 Balanced; ceiling in dB)
                    "limiter": 2, "ceiling_db": 0.0, "dc_blocker": True,
                    # (13g, user 2026-10-29: Kestrel's own defaults; "card" = "backend:name" of cards(), "" = its
                    # pick: changing it sends the soundfonts to the card again)
                    "card": "", "interpolation": 1, "min_velocity": 0}
# (user 2026-10-29, after trying them: When voices run out, Release floor and an Advanced fold (admit, max steal,
# max layers, filter, LFO, mod envelope) were rows too, REMOVED as player settings; _options still takes them
# (steal, release_floor, admit, max_steal, max_layers, filter, lfo, mod_env) at Kestrel's defaults, the connector
# + its bit-exact test (loaded.rs the_sound_rows_*) kept: a row back = its KESTREL_DEFAULTS key, CHOICES / RANGES
# entry, settings.APP_KEYS line, BuiltinPanel row, texts (git: d292586). Filter would also need loaded_for's filter.)
# (the lists' places = the connector's numbers: Nearest / Linear / Cubic)
CHOICES = {"interpolation": 3, "limiter": 3}
LIMITER_ORDER = (2, 1, 0)  # (shown Balanced, Brickwall, Off)
RANGES = {"min_velocity": (0, 127)}
DECIMALS = {"ceiling_db": (-24.0, 0.0)}  # (number boxes taking decimals + minus: "-1", "-0.5")


def card_id(name, backend):
    """A card as saved (App.kestrel_opts["card"]): "vulkan:NVIDIA ..." (Kestrel picks the first card whose name holds it)."""
    return f"{backend}:{name}"


_cards_seen = None  # (cards()' last answer: the graphics cards don't change while Spiderweb runs, mostly)


def cards():
    """The graphics cards Kestrel can play on, best first: [(name, backend, kind)] (kind discrete / integrated /
    virtual; other kinds and OpenGL left out: slower, and Kestrel's own pick never takes them), [] when none,
    None when the connector can't say. Up to a second: off the window's thread (seen_cards() = the last answer)."""
    global _cards_seen
    try:
        with _OPENING:  # (never beside a start: asking the cards opens their drivers too)
            text = load().kb_cards()
    except SynthError:
        return None
    except OSError:
        # (user 2026-11-13: an access violation inside the graphics drivers' listing while a heavy MIDI loaded,
        # once; never asked again: a crash stopped mid-way may leave the drivers' locks held, a second ask could
        # hang under _OPENING. "Can't say" = Automatic only, never a stuck dropdown)
        _cards_seen = []
        return None
    if text is None:
        return None
    out = []
    for line in text.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] and parts[1] != "gl" and parts[2] != "other":
            out.append(tuple(parts))
    _cards_seen = out
    return out


def seen_cards():
    """cards()' last answer, None before it was asked."""
    return _cards_seen


BACKEND_NAMES = {"vulkan": "Vulkan", "dx12": "DirectX 12", "metal": "Metal"}


def card_name(card):
    """A card as the dropdown shows it: "NVIDIA GeForce RTX 3080 Ti (Vulkan)" ("" = None: Automatic)."""
    if not card:
        return None
    backend, _, name = card.partition(":")
    return f"{name} ({BACKEND_NAMES.get(backend, backend)})"


def read_voices(text):
    """Max voices as typed: a number, "300k", "1m", "1mil", "1.5M", "1,000,000" (user 2026-10-28), else ValueError."""
    import math
    import re
    t = text.strip().lower().replace("_", "").replace(" ", "")
    # (a comma before exactly three digits groups thousands, any other is the decimal point: "1,5m" = 1.5 million,
    # was 15 million, hunt 2026-10-28)
    t = re.sub(r",(?=\d{3}(?!\d))", "", t).replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)(k|m|mil|million)?", t)
    if not m or len(m.group(1)) > 30:  # (a pasted 300-digit number overflowed into an error message)
        raise ValueError(text)
    v = float(m.group(1)) * {None: 1, "k": 1e3}.get(m.group(2), 1e6)
    if not math.isfinite(v):
        raise ValueError(text)
    return int(round(v))
PART_TYPE = 0xF5  # (the connector's own status: data1 = channel, data2 = 0 melodic / 1 drums; lib.rs PART_TYPE)
UNKNOWN_LEN = 0xFFFFFFFF  # (push lens: a note whose length isn't known)
# (one Kestrel starting at a time, whatever asks: two starting together in one program can hang on the graphics
# card, seen in the connector's tests 2026-10-27)
_OPENING = threading.Lock()


class _Options(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("sample_rate", ctypes.c_uint32), ("block_frames", ctypes.c_uint32),
                ("ahead_blocks", ctypes.c_uint32), ("max_voices", ctypes.c_uint32),
                ("lower_by_itself", ctypes.c_uint32), ("limiter", ctypes.c_uint32),
                ("limiter_ceiling_db", ctypes.c_float), ("dc_blocker", ctypes.c_uint32),
                ("dc_blocker_hz", ctypes.c_float), ("interpolation", ctypes.c_uint32),
                ("steal_rule", ctypes.c_uint32), ("release_floor_db", ctypes.c_float),
                ("min_velocity", ctypes.c_uint32), ("admit_rule", ctypes.c_uint32),
                ("max_steal_percent", ctypes.c_uint32), ("max_layers", ctypes.c_uint32), ("filter", ctypes.c_uint32),
                ("lfo", ctypes.c_uint32), ("mod_env", ctypes.c_uint32),
                ("clock_seed", ctypes.c_double)]  # (0.9.0 on; older connectors read only the fields before it)


def _options(block=BLOCK, ahead=AHEAD, voices=MAX_VOICES, lower=True, limiter=2, ceiling_db=0.0, dc_blocker=True,
             dc_blocker_hz=0.0, interpolation=1, steal=2, release_floor=-100, min_velocity=0, admit=0, max_steal=25,
             max_layers=16, filter=True, lfo=True, mod_env=True, card="", clock_seed=0.0):
    """The connector's options from Kestrel's keywords (App.kestrel_opts; card = Loaded's, not a synth's).
    clock_seed = a piece's tempo clock starts with this carried (ahead_pieces.Song.clock_at)."""
    return _Options(ctypes.sizeof(_Options), RATE, block, ahead, voices, int(lower), int(limiter), float(ceiling_db),
                    int(dc_blocker), dc_blocker_hz, interpolation, steal, release_floor, min_velocity, admit, max_steal,
                    max_layers, int(filter), int(lfo), int(mod_env), float(clock_seed))


class _Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("rendered", "handed", "silence", "late", "voices",
                                                      "budget_dropped", "kestrel_dropped", "stolen")] + \
               [(name, ctypes.c_uint32) for name in ("last_block_us", "worst_block_us", "allowance", "failed",
                                                      "last_prep_us", "voice_cap", "cuts")] + \
               [(name, ctypes.c_uint64) for name in ("keys_wanted", "keys_kept", "fast_blocks", "merged")] + \
               [(name, ctypes.c_float) for name in ("merge_mean", "merge_max")]


class _PieceStatus(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("state", ctypes.c_uint32), ("frames", ctypes.c_uint64),
                ("progress", ctypes.c_float), ("wall_ms", ctypes.c_uint32)]


_dll = None


def dll_path():
    base = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "kestrel")
    return os.path.join(base, "x64", DLL_NAME) if WINDOWS else os.path.join(base, "linux-x64", SO_NAME)


def available():
    """None when Kestrel can be used here, else the text key saying why not (64-bit Windows / Linux on x86-64)."""
    import platform
    if not (WINDOWS or sys.platform.startswith("linux")) or struct.calcsize("P") != 8 or \
            platform.machine().lower() not in ("amd64", "x86_64"):
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
                            ("kb_fill", [p, p, u32], None), ("kb_close", [p], None),
                            ("kb_font_load", [ctypes.c_char_p, u32], p), ("kb_font_free", [p], None),
                            ("kb_load", [ctypes.POINTER(p), u32], p), ("kb_unload", [p], None),
                            ("kb_font_load_with", [ctypes.c_char_p, u32, u32], p),
                            ("kb_load_on", [ctypes.POINTER(p), u32, ctypes.c_char_p, ctypes.c_char_p], p),
                            ("kb_cards", [], ctypes.c_char_p),
                            ("kb_voices_top", [p, ctypes.POINTER(_Options)], u32),
                            ("kb_open_on", [p, ctypes.POINTER(_Options)], p)):
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    # (Rendered ahead's pieces, connector 0.4.0 on: an older DLL lacks them, pieces_here() says so; pieces as bytes 0.5.0 on)
    for name, args, res in (("kb_piece_start", [p, ctypes.c_char_p, ctypes.POINTER(_Options), u32, ctypes.c_uint64,
                                                ctypes.c_uint64], p),
                            ("kb_piece_start_bytes", [p, ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(_Options), u32,
                                                      ctypes.c_uint64, ctypes.c_uint64], p),
                            ("kb_piece_status", [p, ctypes.POINTER(_PieceStatus)], None),
                            ("kb_piece_wait", [p, u32], u32), ("kb_piece_cancel", [p], None),
                            ("kb_piece_audio", [p, ctypes.POINTER(ctypes.c_uint64)], ctypes.POINTER(ctypes.c_float)),
                            ("kb_piece_error", [p], ctypes.c_char_p), ("kb_piece_free", [p], None),
                            ("kb_repeat_marks", [], u32),
                            ("kb_longest_release", [p, ctypes.POINTER(_Options)], ctypes.c_double),  # (0.8.0 on)
                            # (Kestrel's own output stage for Rendered ahead's joined sound, connector 0.7.0 on)
                            ("kb_stage_new", [ctypes.POINTER(_Options)], p), ("kb_stage_latency", [p], u32),
                            ("kb_stage_process", [p, p, u32], None), ("kb_stage_free", [p], None),
                            # (31-EDO's channel tunings, connector 0.10.0 on: KestrelOut.tune)
                            ("kb_tune", [p, u32, u32, ctypes.c_int32], None)):
        fn = getattr(d, name, None)
        if fn is not None:
            fn.argtypes, fn.restype = args, res
    _dll = d
    return d


class Stage:
    """Kestrel's own output stage (DC blocker + Balanced / Brickwall limiter, exactly as its renders run it) over a
    sound joined by Spiderweb (Rendered ahead, user 2026-11-04: our simpler limiter let a dense wall's fading end go
    dull, official renders level it). Stage.make(...) = None without a connector that has it (0.7.0). process(x) =
    float32 (frames, 2) back, held back `latency` frames."""

    def __init__(self, d, handle):
        self.d, self.handle = d, handle
        self.latency = int(d.kb_stage_latency(handle))

    @classmethod
    def make(cls, limiter=2, ceiling_db=0.0, dc_blocker=True):
        try:
            d = load()
        except SynthError:
            return None
        if getattr(d, "kb_stage_new", None) is None:
            return None
        opts = _options(limiter=limiter, ceiling_db=ceiling_db, dc_blocker=dc_blocker)
        h = d.kb_stage_new(ctypes.byref(opts))
        return cls(d, h) if h else None

    def process(self, x):
        out = np.ascontiguousarray(x, np.float32).copy()
        if len(out) and self.handle:
            self.d.kb_stage_process(self.handle, out.ctypes.data, len(out))
        return out

    def close(self):
        if self.handle:
            self.d.kb_stage_free(self.handle)
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def repeat_marks():
    """Our Merlin reads repeat marks (connector 0.6.0 on, PLAN D2): Merlin pieces may send a stack of identical notes
    once with a count (LoadedSong.midi counted=True). An older connector: written out, as before."""
    try:
        d = load()
    except SynthError:
        return False
    fn = getattr(d, "kb_repeat_marks", None)
    return fn is not None and fn() == 1


def version():
    """The connector's version text ("0.1.0; Kestrel 1.3.0 (...)"), or None when it can't be loaded."""
    try:
        return load().kb_version().decode("utf-8", "replace")
    except SynthError:
        return None


def _error(d, h=None):
    text = d.kb_error(h)
    return text.decode("utf-8", "replace") if text else "?"


def plain_reason(err):
    """Why Kestrel couldn't start / stopped, as a lang key in plain words (hunt 2026-10-28: the connector's text
    said "render with --backend cpu"; old graphics cards and drivers are the usual cause on other PCs)."""
    key = getattr(err, "key", None)
    if key in ("playback.kestrel_missing", "playback.kestrel_not_here", "playback.kestrel_no_sound_device"):
        return key
    text = str(getattr(err, "values", {}).get("err", err)).lower()
    if text == "stuck":  # (App.kestrel_stuck: no new sound while playing, no error from it)
        return "ks.why_stuck"
    if getattr(err, "path", None):  # (reading that soundfont failed: no graphics card was asked yet)
        return "ks.why_memory" if any(w in text for w in ("out of memory", "allocat")) else "ks.why_font"
    for words, why in ((("out of memory", "outofmemory", "allocat"), "ks.why_memory"),
                       (("device lost", "devicelost", "device removed", "device reset"), "ks.why_lost"),
                       (("no usable gpu", "adapter", "no non-software", "vulkan", "dx12", "d3d12", "driver",
                         "request_device", "requestdevice", "gpu"), "ks.why_no_gpu"),
                       (("soundfont", "smpl", ".sf2", ".sfz", "riff", "sample"), "ks.why_font"),
                       (("winerror", "not found", "function", "dll", ".so"), "ks.why_connector"),
                       (("in time", "timed out"), "ks.why_slow")):
        if any(w in text for w in words):
            return why
    return "ks.why_other"


# KEPT LOADED (hot reload, 2026-10-29: reading a soundfont is ~90 % of a start, 5 s for a 390 MB piano): each
# soundfont read once (_banks: path -> (size + time, handle)) and the list stacked on the graphics card once (_kept),
# while Kestrel is the MIDI out; a setting change only opens a new synth on them (~20 ms). Banks are touched under
# _BANKS (taken BEFORE _OPENING, never the other way); handles freed only there.
_BANKS = threading.Lock()
_banks = {}
_kept = None
# ONE START AT A TIME (hunt 2026-10-29: a 2nd start beside a running one read every soundfont twice and put two
# copies on the graphics card): loaded_for holds _STARTING, a later one waits and finds the first one's kept.
# GIVEN UP: stop_starts() bumps _era; a start begun before it stops between soundfonts (never stacked / kept).
_STARTING = threading.Lock()
_era = 0


def _given_up(era):
    """A start begun at era stops: SynthError marked .cancelled (said to nobody)."""
    if era != _era:
        err = SynthError("playback.kestrel_failed", err="start given up")
        err.cancelled = True
        raise err


def stop_starts():
    """Starts on their way give up (another MIDI out, the soundfont list changed)."""
    global _era
    _era += 1


def starting():
    """A start is reading / stacking now."""
    return _STARTING.locked()


def _stamp(path, filter=True):
    """The file's size + time, and whether it's read with its filters (Kestrel decides them while reading: the
    Filter tick = read again, 13g)."""
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns, bool(filter)


def _font_bank(path, filter=True):
    """(called holding _BANKS; let go while it reads) The handle of this soundfont read into memory: the kept one while the
    file is the same, else read now (seconds: the lock let go meanwhile). SynthError with .path when it can't be."""
    d = load()
    try:
        stamp = _stamp(path, filter)
    except OSError as e:
        err = SynthError("playback.kestrel_failed", err=e)
        err.path = path
        raise err from None
    old = _banks.get(path)
    if old is not None and old[0] == stamp:
        return old[1]
    _BANKS.release()  # (reading takes seconds: the window's thread may forget banks meanwhile)
    try:
        h = d.kb_font_load_with(os.fsdecode(path).encode("utf-8"), RATE, int(bool(filter)))
        why = None if h else _error(d)
    finally:
        _BANKS.acquire()
    if not h:
        err = SynthError("playback.kestrel_failed", err=why)
        err.path = path
        raise err
    old = _banks.get(path)
    if old is not None:
        d.kb_font_free(old[1])
    _banks[path] = (stamp, h)
    return h


class Loaded:
    """Spiderweb's soundfont list (top first) stacked and on the graphics card: synths open on it in a moment.
    Made by loaded_for(); SynthError when a soundfont can't be read (.path = that one) or the card can't start."""

    def __init__(self, fonts, era=None, card="", filter=True):
        d = self.dll = load()
        self.fonts = list(fonts)
        self.card, self.filter = card or "", bool(filter)
        self.card_wanted, self.card_why = self.card, None  # (loaded_for: the picked card, why it isn't this one)
        self.handle = None
        era = _era if era is None else era
        with _BANKS:
            while True:  # (each file read only if it isn't kept; again if one was freed while a later one read)
                hs = []
                for f in self.fonts:
                    _given_up(era)  # (between soundfonts: a given-up start reads no more)
                    hs.append(_font_bank(f, filter))
                if all(_banks.get(f, (0, None))[1] == h for f, h in zip(self.fonts, hs)):
                    break
            _given_up(era)
            self.stamps = [_banks[f][0] for f in self.fonts]
            arr = (ctypes.c_void_p * max(1, len(hs)))(*hs)
            backend, _, name = self.card.partition(":")
            with _OPENING:
                h = d.kb_load_on(arr, len(hs), backend.encode("utf-8") or None, name.encode("utf-8") or None)
        if not h:
            raise SynthError("playback.kestrel_failed", err=_error(d))
        self.handle = h

    def fits(self, fonts, card="", filter=True):
        """Loaded for exactly these soundfonts on this card ("" = Kestrel's pick), none changed on disk since."""
        if self.handle is None or list(fonts) != self.fonts or (card or "") != self.card:
            return False
        try:
            return [_stamp(f, filter) for f in fonts] == self.stamps
        except OSError:
            return False

    def voices_top(self, block=BLOCK):
        """The most Max voices this graphics card takes at this block size, or None when not known."""
        if self.handle is None:
            return None
        opts = _options(block=block, ahead=3, voices=1, lower=False)
        with _OPENING:
            top = self.dll.kb_voices_top(self.handle, ctypes.byref(opts)) if self.handle else 0
        return top or None

    def longest_release(self):
        """Seconds the longest note of these soundfonts keeps sounding after its note-off (its release down to
        Kestrel's envelope floor), or None when not known (endless, or a connector before 0.8.0). Asked once."""
        if not hasattr(self, "_release"):
            fn = getattr(self.dll, "kb_longest_release", None)
            got = -1.0
            if fn is not None and self.handle:
                opts = _options(block=BLOCK, ahead=3, voices=1, lower=False)
                with _OPENING:
                    got = fn(self.handle, ctypes.byref(opts)) if self.handle else -1.0
            self._release = got if got >= 0 else None
        return self._release

    def close(self):
        """The graphics card's copy freed (synths open on it keep their own share)."""
        with _OPENING:
            if self.handle:
                self.dll.kb_unload(self.handle)
                self.handle = None


def loaded_for(fonts, card="", filter=True):
    """(any thread) The kept Loaded when it is for these soundfonts (+ card, Filter: Settings → Sound's), else a new
    one (the old one closed FIRST: one copy on the graphics card; only soundfonts not kept read; banks no longer
    listed freed)."""
    global _kept
    era = _era
    with _STARTING:  # (one start at a time: a later one finds the earlier one's kept)
        _given_up(era)
        wanted, card = card, card_in_use(card)
        kept = _kept
        if kept is not None and kept.fits(fonts, card, filter):
            return kept
        drop_loaded()
        forget(keep=fonts)
        try:
            new = Loaded(fonts, era, card, filter)
        except SynthError as e:
            # (the picked card isn't on this PC now, or it can't start (hunt 2026-10-29: "update the driver" at
            # every Play): Kestrel's own pick; the caller sees .card "" and .card_why)
            if not card or getattr(e, "cancelled", False):
                raise
            why = str(e.values.get("err", ""))
            missing = "no gpu adapter matched" in why
            if not missing:
                _bad_cards[card] = why
            new = Loaded(fonts, era, "", filter)
            new.card_why = "missing" if missing else "failed"
        new.card_wanted = wanted
        if era != _era:  # (given up while it was sent to the graphics card: nothing kept)
            new.close()
            _given_up(era)
        _kept = new
        return new


def kept_for(fonts, card="", filter=True):
    """The kept Loaded when it is for these soundfonts (+ card, Filter), else None (never reads)."""
    kept = _kept
    return kept if kept is not None and kept.fits(fonts, card_in_use(card), filter) else None


_bad_cards = {}  # picked cards that couldn't start Kestrel this run: card -> why (tried again next Spiderweb start)


def card_in_use(card):
    """The card Kestrel starts on for this picked one: "" (its own pick) when it failed this run."""
    return "" if card in _bad_cards else card or ""


def drop_loaded():
    """The graphics card's part let go (a failure, the list changed): the soundfonts stay read in memory."""
    global _kept
    kept, _kept = _kept, None
    if kept is not None:
        kept.close()


def forget(keep=None, paths=None):
    """Soundfonts read into memory freed: every one not in keep (None = all), or (paths) these ones."""
    if not _banks:
        return
    with _BANKS:
        for p in list(_banks):
            if (p in paths) if paths is not None else (keep is None or p not in keep):
                _, h = _banks.pop(p)
                load().kb_font_free(h)


def let_go():
    """Everything Kestrel kept freed (another MIDI out picked); starts on their way give up."""
    stop_starts()
    drop_loaded()
    forget()


# RENDERED AHEAD's pieces (PLAN.md stage M): a small MIDI file made into audio by Merlin (Kestrel 1.4.0's per-key
# renderer, drops no note) or Kestrel, on a Loaded's soundfonts, in the connector's own thread. Same samples as
# Kestrel's own render of that file (dev/scratch/piece_check.py). Merlin refuses 31-EDO, Velocity threshold and 2+
# soundfonts for now (FAILED + .error): the caller sends those to Kestrel.
PIECE_BLOCK = 4096  # (Kestrel's render default: fewer, bigger blocks; Merlin's partition is min(block, 2048))
ENGINES = {"kestrel": 0, "merlin": 1}
RUNNING, DONE, FAILED, CANCELLED = 0, 1, 2, 3


def pieces_here():
    """The connector can make pieces (an older DLL can't)."""
    try:
        return hasattr(load(), "kb_piece_start")
    except SynthError:
        return False


class Piece:
    """One piece being made (starts at once, returns at once). loaded = a Loaded; midi = path of the piece's MIDI
    (kept until close(); delete_midi = removed then); skip = frames dropped at the start (the piece starts early so
    notes already ringing sound right), most = frames kept after them (0 = to the end, ring-out included); opts =
    _options keywords (limiter Off by default: Spiderweb's own runs over the joined pieces). SynthError when it
    can't start. close() waits for the connector's thread (Merlin stops within ~0.3 s): not on the window's thread."""

    def __init__(self, loaded, midi, engine="merlin", skip=0, most=0, delete_midi=False, **opts):
        d = self.dll = load()
        if not pieces_here():
            raise SynthError("playback.kestrel_failed", err="this connector can't render pieces")
        self.midi, self.delete_midi, self.engine = midi, delete_midi, engine
        o = dict(block=PIECE_BLOCK, limiter=0, dc_blocker=False)
        o.update(opts)
        self._opts = _options(**o)  # (kept alive while the call reads it)
        if loaded is None or not loaded.handle:
            raise SynthError("playback.kestrel_failed", err="no soundfonts loaded")
        if isinstance(midi, (bytes, bytearray)) and not hasattr(d, "kb_piece_start_bytes"):
            fd, path = tempfile.mkstemp(".mid", "spiderweb-piece-")  # (an older connector: a file it can read)
            with os.fdopen(fd, "wb") as f:
                f.write(midi)
            self.midi, self.delete_midi, midi = path, True, path
        if isinstance(midi, (bytes, bytearray)):  # (the piece's MIDI never on disk: the connector copies it)
            self.midi = None
            # (a bytearray handed over in place, never copied first: the connector makes its own copy, M1)
            held = (ctypes.c_char * len(midi)).from_buffer(midi) if isinstance(midi, bytearray) else midi
            self.handle = d.kb_piece_start_bytes(loaded.handle, held, len(midi), ctypes.byref(self._opts),
                                                 ENGINES[engine], int(skip), int(most))
            del held
        else:
            self.handle = d.kb_piece_start(loaded.handle, os.fsdecode(midi).encode("utf-8"), ctypes.byref(self._opts),
                                           ENGINES[engine], int(skip), int(most))
        if not self.handle:
            raise SynthError("playback.kestrel_failed", err=_error(d))
        self._lock = threading.Lock()

    def status(self):
        """{"state": RUNNING / DONE / FAILED / CANCELLED, "frames": kept so far (Merlin: at the end), "progress":
        0..1, "seconds": the time it took (0 while running)}."""
        st = _PieceStatus(ctypes.sizeof(_PieceStatus))
        with self._lock:
            if not self.handle:
                return {"state": CANCELLED, "frames": 0, "progress": 0.0, "seconds": 0.0}
            self.dll.kb_piece_status(self.handle, ctypes.byref(st))
        return {"state": st.state, "frames": st.frames, "progress": st.progress, "seconds": st.wall_ms / 1000}

    def wait(self, timeout=None):
        """Wait until it's finished (or timeout seconds); its state."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:  # (short waits: the lock isn't held long, close() can run between them)
            left = 0.05 if end is None else max(0.0, min(0.05, end - time.monotonic()))
            with self._lock:
                if not self.handle:
                    return CANCELLED
                state = self.dll.kb_piece_wait(self.handle, int(left * 1000))
            if state != RUNNING or (end is not None and time.monotonic() >= end):
                return state

    def cancel(self):
        """Ask it to stop (it turns CANCELLED soon)."""
        with self._lock:
            if self.handle:
                self.dll.kb_piece_cancel(self.handle)

    def audio(self):
        """The finished audio: float32 array (frames, 2), a copy (None before DONE)."""
        n = ctypes.c_uint64()
        with self._lock:
            if not self.handle:
                return None
            ptr = self.dll.kb_piece_audio(self.handle, ctypes.byref(n))
            if not ptr:
                return None
            if not n.value:
                return np.zeros((0, 2), np.float32)
            return np.ctypeslib.as_array(ptr, shape=(n.value * 2,)).reshape(-1, 2).copy()

    def error(self):
        """Why it FAILED (text), None if it didn't."""
        with self._lock:
            text = self.dll.kb_piece_error(self.handle) if self.handle else None
        return text.decode("utf-8", "replace") if text else None

    def close(self):
        """Stop it if running, free it and its audio (waits for its thread), remove the MIDI if asked."""
        with self._lock:
            h, self.handle = self.handle, None
        if h:
            self.dll.kb_piece_free(h)
        if self.delete_midi:
            try:
                os.remove(self.midi)
            except OSError:
                pass
            self.delete_midi = False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class Kestrel:
    """One Kestrel synth, playing (silence while nothing is sent) from open until close(). fonts = Spiderweb's
    soundfont list, top first (each overrides the ones above it); loaded = a Loaded for them (opens in a moment),
    None = read and stacked just for this synth. synth = files.synth.Synth (BASS + the sound device). Opening
    without loaded takes seconds (the soundfonts load): not on the window's thread. SynthError when it can't."""

    def __init__(self, synth, fonts, block=BLOCK, ahead=None, loaded=None, **sound):
        """sound = _options' keywords (Max voices, Lower it by itself, Limiter, the sound rows...)."""
        d = self.dll = load()
        self.bass = synth.bass
        self.block = block
        if ahead is None:  # (about the same time made ahead at every block size: AHEAD of 512, 3 blocks at least)
            ahead = max(3, -(-AHEAD * BLOCK // block))
        self.ahead = ahead
        opts = _options(block=block, ahead=ahead, **sound)
        names = (ctypes.c_char_p * max(1, len(fonts)))(*[os.fsdecode(f).encode("utf-8") for f in fonts])
        with _OPENING:
            if loaded is None:
                h = d.kb_open(names, len(fonts), ctypes.byref(opts))
            elif loaded.handle:
                h = d.kb_open_on(loaded.handle, ctypes.byref(opts))
            else:  # (let go meanwhile: a start given up, said to nobody)
                err = SynthError("playback.kestrel_failed", err="start given up")
                err.cancelled = True
                raise err
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

    def tune(self, tunings):
        """[(channel 0-255, 12 cents)] (playback.edo_tuning): each note name of each channel tuned from the next block
        on (31-EDO). False: the connector is too old (no kb_tune)."""
        fn = getattr(self.dll, "kb_tune", None)
        if fn is None or not self.handle:
            return False
        for ch, cents in tunings:
            for pc, c in enumerate(cents):
                fn(self.handle, int(ch), pc, int(round(c * 2048)))
        return True

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
GAP_S = 0.01  # (KestrelOut.waited: how often it asks the connector for the silence handed out)


class KestrelOut:
    """Kestrel as a MIDI out (playback.MidiOut's handle): send() = sounds now (Stop's note-offs); a player that knows
    what comes sends `lead` seconds early with send_at(msg, when) = the exact perf_counter time it should sound,
    stamped with that frame, so notes keep their exact time (not one block's). Messages are pushed in whole blocks
    (flush): a busy block's note-ons ranked together. track = the channel + port (the note budget shares by it)."""

    lead = LEAD

    def __init__(self, synth, fonts, loaded=None, **kw):
        self.k = Kestrel(synth, fonts, loaded=loaded, **kw)
        self.opts = dict(kw)  # (App.fresh_builtin: other settings = opened again)
        # (sent ahead past what's made ahead + 3 blocks: at block 2048 Kestrel kept ~230 ms made and own shapes sent
        # 200 ms ahead all came late, on a 43 ms grid, hunt 2026-10-28)
        self.lead = max(LEAD, (self.k.ahead + 3) * self.k.block / RATE)
        self.font_paths = list(fonts)  # (App.fresh_builtin: another list picked = opened again)
        self._lock = threading.Lock()
        self._frames, self._msgs, self._lens = [], [], []
        self._offs = []  # (frame, msg) note-offs pushed ahead: sent again by cancel()
        self._own = []  # (frame, msg, length) pushed ahead: taken back and pushed again at a new tempo (retime)
        self.late = 0  # messages that came for a frame already made (send() = now: Stop's, mostly)
        self.resync()

    def resync(self):
        """Frames against the clock again (each play's start: the sound card's clock drifts from perf_counter)."""
        self._t0, self._f0 = self.k.anchor()
        self._gaps, self._gap_at = self.k.stats()["silence"], 0.0

    def waited(self):
        """Seconds of silence Kestrel handed out since the last look (it fell behind: every later sound comes that much
        later), the frames' clock moved on by as much, so messages sent for a time keep their frames. The caller moves
        the play line on by the same (13e, user 2026-10-28: the line waits too, always). Looks every GAP_S at most."""
        now = time.perf_counter()
        if now - self._gap_at < GAP_S:
            return 0.0
        self._gap_at = now
        got = self.k.stats()["silence"]
        if got <= self._gaps:
            return 0.0
        d = (got - self._gaps) / RATE
        self._gaps = got
        self._t0 += d
        return d

    def frame_at(self, when):
        return max(0, int(round(self._f0 + (when - self._t0) * RATE)))

    def send(self, msg):
        self.send_at(msg, time.perf_counter())
        self.flush(everything=True)

    def tune(self, tunings):
        """31-EDO's channels tuned (Kestrel.tune; playback.MidiOut.tune_edo), messages waiting flushed first."""
        self.flush(everything=True)
        return self.k.tune(tunings)

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
                b = self.k.block
                edge = self.frame_at(time.perf_counter() + self.lead) // b * b
                n = next((i for i, f in enumerate(self._frames) if f >= edge), len(self._frames))
                if not n:
                    return
            fr, ms, ln = self._frames[:n], self._msgs[:n], self._lens[:n]
            del self._frames[:n], self._msgs[:n], self._lens[:n]
            if len(self._offs) > 4096:  # (only the ones still ahead are ever needed)
                now = self.k.frame()
                self._offs = [o for o in self._offs if o[0] >= now]
            made = self.k.stats()["rendered"]
            self._own = [o for o in self._own if o[0] >= made] + list(zip(fr, ms, ln))
        tracks = [(m & 15) | ((m >> 24) & 15) << 4 for m in ms]
        made = self.k.stats()["rendered"]
        if fr[0] < made:  # (for a frame already made: sounds at the next block's start, a little late)
            self.late += sum(f < made for f in fr)
        self.k.push(fr, ms, tracks, ln)

    def retime(self, at, convert):
        """The tempo changed while playing (hunt 2026-10-28: own shapes kept ~0.2 s at the old speed, a note-off
        could land before its note-on and ring on; a loaded MIDI's retime took them back for good): what this out
        sent for frame `at` on, pushed ahead (Kestrel.cut has taken it back) or not yet, again at convert(frame) (in
        order: a note's off stays after its on)."""
        with self._lock:
            got = [o for o in self._own if o[0] >= at]
            self._own = [o for o in self._own if o[0] < at]
            got += list(zip(self._frames, self._msgs, self._lens))
            self._frames, self._msgs, self._lens = [], [], []
            moved = sorted(((max(at, int(convert(f))), m, ln) for f, m, ln in got), key=lambda o: o[0])
            self._offs = [o for o in self._offs if o[0] < at]
            for f, m, ln in moved:
                self._frames.append(f)
                self._msgs.append(m)
                self._lens.append(ln)
                if m & 0xF0 == 0x80 or (m & 0xF0 == 0x90 and not (m >> 16) & 0x7F):
                    self._offs.append((f, m))
        self.flush()

    def cancel(self):
        """Everything sent ahead forgotten (Stop): its note-offs sound now instead (the player counted those notes
        as ended), so nothing is left ringing."""
        with self._lock:
            offs = [m for f, m in self._offs if f >= self.k.frame()]
            offs += [m for m in self._msgs if m & 0xF0 == 0x80 or (m & 0xF0 == 0x90 and not (m >> 16) & 0x7F)]
            self._frames, self._msgs, self._lens, self._offs, self._own = [], [], [], [], []
        self.k.cancel()
        now = self.frame_at(time.perf_counter())
        if offs:
            self.k.push([now] * len(offs), offs, [(m & 15) | ((m >> 24) & 15) << 4 for m in offs])

    def close(self):
        self.k.close()
