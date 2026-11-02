"""Rendered ahead's player (PLAN.md R3): plays the blocks files/ahead_pieces.py made, joined, through the sound device.

How it fits in: the MIDI out "Rendered ahead" is a Sink (takes every message, sends nothing), so the usual player
(files/playback.Player) still runs the play line, page turns and edits while playing; AheadPlay makes and plays the
sound, and the window's play tick moves the play line to what is HEARD (Player.follow), so the line waits when the
sound waits.

At the grey (a block not made yet): Slow down to keep up ON = the sound waits (stutters) until it's made; OFF
(default) = skips: silence at the song's speed, the maker jumps past the line (it makes from where the sound is).
The first block is always waited for (play starts as soon as it's made). Stop fades (synth.FADE_MS).
Loudness: OUR limiter over the joined pieces (pieces are made without one: no level jumps at the joins),
App.ahead_opts limiter 0 Off / 1 Brickwall / 2 Balanced + ceiling_db.
Lower it by itself (R1c, Kestrel + BASSMIDI): sound made ahead under LOW_SHARE of Render ahead (at least
LOW_LEAST s) = the next pieces' voice limit x LOWER_STEP; above RISE_SHARE = back up a step every RISE_EVERY s."""

import math
import threading
import time

import numpy as np

from files import errors
from files.ahead_pieces import BLOCK, Maker, Song
from files.synth import RATE, Player, Synth, SynthError

FLOOR_VOICES = {"voices": 1024, "bass_voices": 50}  # lowering never goes under these
RISE_EVERY = 2.0  # seconds between steps back up
FOLLOW_S = 0.02  # the play line moved to the heard sound when it's further off than this


class AheadLimiter:
    """Keeps the joined sound under the ceiling (looking one block ahead, nothing gets through). Brickwall comes back
    up quickly (FAST s), Balanced slowly (SLOW s: less pulling at low notes). Both HOLD the gain HOLD_S first. Off =
    the sound as it is (it may clip)."""
    BLOCK = 64
    DELAY = 2 * BLOCK
    FAST, SLOW, HOLD_S = 0.05, 0.3, 0.02

    def __init__(self, mode=2, ceiling_db=0.0):
        self.mode = mode
        self.top = 10 ** (ceiling_db / 20)
        self.inp = np.zeros((self.BLOCK, 2), np.float32)
        self.out = [np.zeros((self.BLOCK, 2), np.float32)]
        self.env, self.gain, self.held = self.top, 1.0, 0
        release = self.FAST if mode == 1 else self.SLOW
        self.fall = math.exp(-self.BLOCK / (release * RATE))
        self.hold = max(1, int(self.HOLD_S * RATE / self.BLOCK))

    def process(self, x):
        if self.mode == 0:  # (Off: as it is, the same delay so the play line's sums stay right)
            inp = self.inp = np.concatenate([self.inp, x])
            out, self.inp = inp[:len(x)], inp[len(x):]
            return out
        B = self.BLOCK
        inp = self.inp = np.concatenate([self.inp, x])
        k = len(inp) // B - 1
        if k > 0:
            peaks = np.abs(inp[:(k + 1) * B]).reshape(k + 1, -1).max(axis=1)
            ahead = np.maximum(peaks[:-1], peaks[1:])
            env, fall, top, held = self.env, self.fall, self.top, self.held
            gains = np.empty(k + 1)
            gains[0] = self.gain
            for i in range(k):
                a = float(ahead[i])
                if a >= env:
                    env, held = a, self.hold
                elif held:
                    held -= 1
                else:
                    env = max(a, env * fall)
                gains[i + 1] = top / max(env, top)
            self.env, self.gain, self.held = env, gains[-1], held
            t = (np.arange(1, B + 1) / B)[None, :]
            ramp = (gains[:-1, None] * (1 - t) + gains[1:, None] * t).reshape(-1, 1)
            self.out.append(inp[:k * B] * ramp.astype(np.float32))
            self.inp = inp[k * B:]
        out = np.concatenate(self.out)
        self.out = [out[len(x):]]
        return out[:len(x)]


class Sink:
    """The MIDI out "Rendered ahead" for the usual player: messages go nowhere (AheadPlay makes the sound)."""
    lead = 0

    def send(self, msg):
        pass

    def close(self):
        pass


class AheadPlay:
    """Owned by the App (app.ahead_play). The Maker is kept between plays (an unchanged song plays at once from
    what's made) and dropped when the engine / its settings / the soundfonts change."""

    def __init__(self, app):
        self.app = app
        self.maker = None
        self.key = None  # (engine, settings, soundfonts, card) the maker was made for
        self.audio = None  # synth.Player
        self.play_at = 0  # the song frame handed to the sound next
        self.started = False  # (the first block has been played: from then on Slow down OFF skips)
        self.load = None  # a thread opening BASSMIDI's soundfonts
        self.factor = 1.0  # (Lower it by itself: the voice limit's share in use)
        self._rose = 0.0
        self.skipped = 0  # frames skipped (silence) since Play
        self.slow = False  # (the Slow down tick, copied each tick for the sound's thread)

    # ------------------------------------------------------------ settings

    def _want_key(self):
        from window.hz_preview import font_list
        from files.render_ahead import engine_used
        app = self.app
        opts = {k: v for k, v in app.ahead_opts.items() if k not in ("lower", "limiter", "ceiling_db")}
        return (engine_used(app.ahead_engine), tuple(sorted(opts.items())), tuple(font_list(app.hz_preview)),
                app.kestrel_opts.get("card", ""))

    def _ensure_maker(self):
        """The Maker for the current settings (a new one when they changed). Returns an error text or None."""
        key = self._want_key()
        if self.maker is not None and key == self.key:
            return None
        self.drop()
        engine, _, fonts, card = key
        synth = None
        if engine == "bassmidi":
            try:
                if self.app.synth is None:
                    self.app.synth = Synth()
            except SynthError as e:
                return str(e)
            synth = self.app.synth
            if synth.needs_open(list(fonts)) and not (self.load and self.load.is_alive()):
                self.load = threading.Thread(target=self._open_fonts, args=(synth, list(fonts)), daemon=True)
                self.load.error = None
                self.load.start()
        self.maker = Maker(engine, self.app.ahead_opts, fonts, card, synth)
        self.key = key
        self.factor = 1.0
        errors.note("Rendered ahead", f"{engine}, ahead {self.app.ahead_seconds} s")
        return None

    def _open_fonts(self, synth, fonts):
        try:
            synth.set_fonts(fonts)
        except SynthError as e:
            self.load.error = str(e)

    def drop(self):
        """Everything made let go (settings changed, MIDI out changed, closing)."""
        self.stop()
        if self.maker is not None:
            self.maker.close()
        self.maker, self.key = None, None

    # ------------------------------------------------------------ playing

    def start(self, rendered, ccs, ports, ppq, bpm, tempo, start_beat):
        """Play from start_beat. Returns an error text, or None."""
        err = self._ensure_maker()
        if err:
            return err
        song = Song(rendered, ccs, ports, self.app.picture_use10, ppq, bpm, tempo)
        if self.maker.song is None or not same_song(self.maker.song, song):
            self.maker.set_song(song)
        try:
            if self.app.synth is None:
                self.app.synth = Synth()
        except SynthError as e:
            return str(e)
        if not self.app.synth.can_play:
            from files.lang import tr
            return tr("synth.no_device")
        self.stop()
        self.play_at = max(0, song.frame(int(round(start_beat * ppq))))
        self.started, self.skipped = False, 0
        from files.render_ahead import take_opts
        o = take_opts(self.app.ahead_opts)
        try:
            self.audio = Player(self.app.synth, self.pull, 1.0, limiter=AheadLimiter(o["limiter"], o["ceiling_db"]))
        except SynthError as e:
            self.audio = None
            return str(e)
        self.audio.play(self.play_at)
        self.tick()
        return None

    def changed(self, rendered, ccs, ports, ppq, bpm, tempo):
        """The notes / tempo changed while playing (R5 makes only the changed stretch again)."""
        if self.maker is None:
            return
        song = Song(rendered, ccs, ports, self.app.picture_use10, ppq, bpm, tempo)
        if self.maker.song is None or not same_song(self.maker.song, song):
            self.maker.set_song(song)

    def playing(self):
        return self.audio is not None

    def pull(self, n):
        """(sound's thread) The next frames: made ones, None = wait, silence = skipped (Slow down off)."""
        m = self.maker
        if m is None or m.song is None:
            return np.zeros((0, 2), np.float32)
        got = m.take(self.play_at, n)
        if got is None:
            if not self.started or self.slow:  # (read on the window's thread: Tk can't be asked from here)
                return None
            k = min(n, BLOCK - self.play_at % BLOCK)  # (to the block's end: the next may be made)
            self.play_at += k
            self.skipped += k
            return np.zeros((k, 2), np.float32)
        if len(got):
            self.started = True
        self.play_at += len(got)
        return got

    def tick(self):
        """(window's thread, every play tick) Pieces looked after; the voice limit lowered / raised. Returns an
        error text when making failed (the caller stops and says it), else None."""
        m = self.maker
        if m is None:
            return None
        self.slow = self.app.slow_ahead.get()
        if self.load is not None:
            if self.load.is_alive():
                return None
            err, self.load = self.load.error, None
            if err:
                return err
        self._lower()
        m.step(self.play_at, self.app.ahead_seconds)
        return m.error

    def _lower(self):
        from files.render_ahead import LOW_LEAST, LOW_SHARE, LOWER_STEP, RISE_SHARE
        m = self.maker
        if m.engine == "merlin" or not self.app.ahead_opts.get("lower") or self.audio is None:
            if self.factor != 1.0:
                self._set_factor(1.0)
            return
        made = m.made_ahead(self.play_at)
        ahead = self.app.ahead_seconds
        now = time.monotonic()
        if made < max(LOW_LEAST, LOW_SHARE * ahead):
            if now - self._rose > RISE_EVERY / 2:  # (one step per half RISE_EVERY at most)
                self._set_factor(self.factor * LOWER_STEP)
                self._rose = now
        elif made > RISE_SHARE * ahead and self.factor < 1.0 and now - self._rose > RISE_EVERY:
            self._set_factor(min(1.0, self.factor / LOWER_STEP))
            self._rose = now

    def _set_factor(self, f):
        m = self.maker
        key = "bass_voices" if m.engine == "bassmidi" else "voices"
        full = self.app.ahead_opts[key]
        f = max(f, min(1.0, FLOOR_VOICES[key] / max(1, full)))
        if f == self.factor:
            return
        self.factor = f
        m.opts[key] = max(1, int(full * f))
        m.lowered = f < 1.0

    def grey_beats(self, b0, b1):
        """(R4) [(from, to)] beats between b0 and b1 not made yet, while playing (stopped: none; nothing is made
        then, a whole song in grey would only be in the way)."""
        m = self.maker
        song = None if m is None else m.song
        if self.audio is None or song is None or not len(song) or b1 <= b0:
            return []
        ppq = song.ppq
        f0, f1 = song.frame(max(0, int(b0 * ppq))), song.frame(max(0, int(math.ceil(b1 * ppq))))
        return [(song.map.tick(a / RATE) / ppq, song.map.tick(b / RATE) / ppq) for a, b in m.grey(f0, f1)]

    def heard_sec(self):
        """Song seconds heard right now (None: silence while waiting for the first / next block)."""
        if self.audio is None:
            return None
        f = self.audio.position()
        return None if f is None else f / RATE

    def stop(self):
        if self.audio is not None:
            self.audio.stop()
            self.audio = None
        if self.factor != 1.0 and self.maker is not None:
            self._set_factor(1.0)


def same_song(a, b):
    """True when two Songs would sound the same (notes, CC lines, tempo)."""
    if a.ppq != b.ppq or a.tempo != b.tempo or a.ccs != b.ccs or len(a) != len(b):
        return False
    return all(np.array_equal(getattr(a, k), getattr(b, k)) for k in ("start", "end", "key", "vel", "chan", "port"))
